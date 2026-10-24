"""Google Calendar and Microsoft Outlook Calendar: two-way sync with AIVA meetings.
Google Meet links ride on the Google Calendar connection.

Each person connects their own calendar. Then:

* **AIVA → calendar.** Meetings a person organizes in AIVA are created,
  updated and cancelled in their calendar (``push_event`` / ``cancel_event``,
  called from the meetings API through ``providers.meetings``). By default the
  calendar sends the invitations, so guests get a normal Google / Outlook invite
  and their replies flow back; AIVA then skips its own .ics email.
* **Calendar → AIVA.** Every few minutes (``run_due``) AIVA reads what changed
  (Google ``syncToken``, Graph ``calendarView/delta``) in a window from two weeks
  back to three months ahead. New customer meetings become AIVA meetings linked
  to the matching contact, account or lead (which events count is the person's
  ``import_mode``); changes, RSVPs and cancellations update the AIVA meeting.
* **Failures** don't lose work: a push that couldn't reach the provider is kept
  as ``pending`` on the meeting and retried with backoff.

The link between a meeting and its calendar event lives in
``meeting.meeting_metadata["calendar"]``; AIVA-scheduled events in Google use
the deterministic event ID ``aiva<meeting id hex>`` so every attendee's copy can
be recognised.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection, IntegrationToggle
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.membership import OrganizationMembership
from app.models.user import User

logger = logging.getLogger("aiva.integrations.calendars")

GOOGLE = "https://www.googleapis.com/calendar/v3"
GRAPH = "https://graph.microsoft.com/v1.0/me"

KEYS = ("google_calendar", "outlook_calendar")
LABEL = {"google_calendar": "Google Calendar", "outlook_calendar": "Outlook Calendar"}

SYNC_EVERY = timedelta(minutes=10)
PAST_DAYS = 14
FUTURE_DAYS = 90
MAX_PAGES = 8
JOB_BATCH = 10
MAX_PUSH_ATTEMPTS = 6
IMPORT_MODES = ("matched", "external", "all")

DEFAULTS: dict[str, Any] = {
    "push_meetings": True,  # add meetings I schedule in AIVA to this calendar
    "invites_from_calendar": True,  # let the calendar send the invitations (instead of AIVA's .ics email)
    "pull_events": True,  # bring calendar events into AIVA
    "import_mode": "matched",  # matched: with a contact or lead · external: anyone outside the company · all
    "calendar_id": None,  # None = the primary calendar
}

SKIP_GOOGLE_TYPES = {"outOfOffice", "focusTime", "workingLocation", "birthday"}
GOOGLE_RESPONSES = {"accepted": "accepted", "declined": "declined", "tentative": "tentative", "needsAction": "needs_action"}
GRAPH_RESPONSES = {
    "accepted": "accepted", "organizer": "accepted", "declined": "declined",
    "tentativelyAccepted": "tentative", "notResponded": "needs_action", "none": "needs_action",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def settings_of(connection: IntegrationConnection | None) -> dict[str, Any]:
    config = dict(connection.config or {}) if connection else {}
    merged = {**DEFAULTS, **{k: config[k] for k in DEFAULTS if k in config}}
    if merged["import_mode"] not in IMPORT_MODES:
        merged["import_mode"] = "matched"
    return merged


def _google_calendar_path(settings: dict[str, Any]) -> str:
    return quote(settings.get("calendar_id") or "primary", safe="")


def _graph_events_path(settings: dict[str, Any]) -> str:
    calendar_id = settings.get("calendar_id")
    return f"{GRAPH}/calendars/{quote(calendar_id, safe='')}/events" if calendar_id else f"{GRAPH}/events"


def aiva_event_id(meeting_id: uuid.UUID) -> str:
    """Google event IDs use base32hex characters (a-v, 0-9); a UUID's hex digits qualify."""

    return f"aiva{meeting_id.hex}"


def _meeting_from_event_id(event_id: str | None) -> uuid.UUID | None:
    if not event_id or not event_id.startswith("aiva") or len(event_id) < 36:
        return None
    try:
        return uuid.UUID(event_id[4:36])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Which calendar a meeting goes to
# ---------------------------------------------------------------------------


async def usable_connection(db: AsyncSession, organization_id: uuid.UUID, key: str, user_id: uuid.UUID | None) -> IntegrationConnection | None:
    """The person's calendar connection when the integration is on and signed in, else None."""

    if user_id is None or not await core.is_enabled(db, organization_id, key):
        return None
    connection = await core.connection_for(db, organization_id, key, user_id)
    if connection is None or connection.status == "needs_reauth":
        return None
    return connection


async def default_target(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None) -> tuple[str, IntegrationConnection, dict[str, Any]] | None:
    """The calendar new AIVA meetings are added to: the organizer's connected calendar with pushing on."""

    for key in KEYS:
        connection = await usable_connection(db, organization_id, key, user_id)
        if connection is not None:
            settings = settings_of(connection)
            if settings["push_meetings"]:
                return key, connection, settings
    return None


async def require_target(db: AsyncSession, organization_id: uuid.UUID, key: str, user_id: uuid.UUID | None) -> tuple[str, IntegrationConnection, dict[str, Any]]:
    """A specific calendar, needed for a Google Meet or Teams link. Raises with what to do."""

    connection = await core.active_connection(db, organization_id, key, user_id)
    return key, connection, settings_of(connection)


def _own_email(connection: IntegrationConnection) -> str:
    return (connection.account_label or "").strip().lower()


# ---------------------------------------------------------------------------
# AIVA → calendar
# ---------------------------------------------------------------------------


def _guests(meeting: Meeting, own: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen = {own}
    for attendee in meeting.attendees or []:
        address = (attendee.get("email") or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        out.append({"email": address, "name": attendee.get("name") or address})
    return out


def _body_text(meeting: Meeting, with_link: bool) -> str:
    text = meeting.description or ""
    if with_link and meeting.meeting_url and meeting.meeting_url not in text:
        text = f"{text}\n\nJoin: {meeting.meeting_url}".strip()
    return text[:8000]


def _local_date(moment: datetime, zone: ZoneInfo | None) -> date:
    return moment.astimezone(zone or timezone.utc).date()


def _google_body(meeting: Meeting, *, own: str, attendees: bool, conference: str | None) -> dict[str, Any]:
    zone = _zone(meeting.timezone)
    end = meeting.end_at or meeting.start_at + timedelta(hours=1)
    if meeting.is_all_day:
        first = _local_date(meeting.start_at, zone)
        last = max(_local_date(end, zone), first + timedelta(days=1))
        when = {"start": {"date": first.isoformat()}, "end": {"date": last.isoformat()}}
    else:
        tz_name = meeting.timezone if zone else "UTC"
        when = {"start": {"dateTime": _iso(meeting.start_at), "timeZone": tz_name}, "end": {"dateTime": _iso(end), "timeZone": tz_name}}
    managed_link = conference == "google_meet" or ((meeting.meeting_metadata or {}).get("conference") or {}).get("type") == "google_meet"
    reminder = (meeting.meeting_metadata or {}).get("reminder_minutes")
    body: dict[str, Any] = {
        "summary": meeting.title,
        "description": _body_text(meeting, with_link=not managed_link),
        "location": (meeting.location or ("" if managed_link else meeting.meeting_url) or "")[:1000],
        **when,
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": int(reminder)}]} if reminder is not None else {"useDefault": True},
        "extendedProperties": {"private": {"aiva_meeting_id": str(meeting.id)}},
        "source": {"title": "AIVA CRM", "url": f"{get_settings().app_base_url.rstrip('/')}/meetings?open={meeting.id}"},
        "status": "confirmed",
    }
    if attendees:
        body["attendees"] = [{"email": g["email"], "displayName": g["name"]} for g in _guests(meeting, own)]
    if conference == "google_meet":
        body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


def _graph_time(moment: datetime, zone_name: str) -> dict[str, str]:
    zone = _zone(zone_name) or timezone.utc
    return {"dateTime": moment.astimezone(zone).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": zone_name if _zone(zone_name) else "UTC"}


def _outlook_body(meeting: Meeting, *, own: str, attendees: bool, conference: str | None) -> dict[str, Any]:
    end = meeting.end_at or meeting.start_at + timedelta(hours=1)
    zone_name = meeting.timezone if _zone(meeting.timezone) else "UTC"
    if meeting.is_all_day:
        zone = _zone(zone_name)
        first = _local_date(meeting.start_at, zone)
        last = max(_local_date(end, zone), first + timedelta(days=1))
        start_value = {"dateTime": f"{first.isoformat()}T00:00:00", "timeZone": zone_name}
        end_value = {"dateTime": f"{last.isoformat()}T00:00:00", "timeZone": zone_name}
    else:
        start_value, end_value = _graph_time(meeting.start_at, zone_name), _graph_time(end, zone_name)
    managed_link = conference == "microsoft_teams" or ((meeting.meeting_metadata or {}).get("conference") or {}).get("type") == "microsoft_teams"
    reminder = (meeting.meeting_metadata or {}).get("reminder_minutes")
    body: dict[str, Any] = {
        "subject": meeting.title,
        "body": {"contentType": "text", "content": _body_text(meeting, with_link=not managed_link)},
        "start": start_value,
        "end": end_value,
        "isAllDay": bool(meeting.is_all_day),
        "location": {"displayName": (meeting.location or ("" if managed_link else meeting.meeting_url) or "")[:1000]},
        "isReminderOn": reminder is not None,
        "reminderMinutesBeforeStart": int(reminder or 0),
    }
    if attendees:
        body["attendees"] = [{"emailAddress": {"address": g["email"], "name": g["name"]}, "type": "required"} for g in _guests(meeting, own)]
    if conference == "microsoft_teams":
        body["isOnlineMeeting"] = True
        body["onlineMeetingProvider"] = "teamsForBusiness"
    return body


def safe_url(value: Any) -> str | None:
    """Links from providers end up in <a href>: only https, nothing else (no javascript:, data:, tel:)."""

    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:1000] if value.lower().startswith("https://") else None


def _google_join(event: dict[str, Any]) -> str | None:
    for entry in (event.get("conferenceData") or {}).get("entryPoints") or []:
        if entry.get("entryPointType") == "video" and safe_url(entry.get("uri")):
            return safe_url(entry["uri"])
    return safe_url(event.get("hangoutLink"))


def _result(key: str, event: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if key == "google_calendar":
        return {
            "event_id": event.get("id"),
            "ical_uid": event.get("iCalUID"),
            "etag": event.get("etag"),
            "updated": event.get("updated"),
            "html_link": safe_url(event.get("htmlLink")),
            "join_url": _google_join(event),
            "calendar_id": settings.get("calendar_id") or "primary",
        }
    return {
        "event_id": event.get("id"),
        "ical_uid": event.get("iCalUId"),
        "etag": event.get("changeKey"),
        "updated": event.get("lastModifiedDateTime"),
        "html_link": safe_url(event.get("webLink")),
        "join_url": safe_url((event.get("onlineMeeting") or {}).get("joinUrl")) or safe_url(event.get("onlineMeetingUrl")),
        "calendar_id": settings.get("calendar_id"),
    }


async def push_event(
    db: AsyncSession,
    connection: IntegrationConnection,
    meeting: Meeting,
    *,
    event_id: str | None,
    invites: bool,
    notify: bool,
    conference: str | None = None,
    remove_conference: bool = False,
) -> dict[str, Any]:
    """Create (``event_id`` None) or update the meeting's event. Returns the event's identifiers and join link.

    ``invites``: the calendar owns the invitations (guests are on the event and
    get updates); otherwise Google keeps the guest list silently and Outlook gets
    no guests, so Exchange doesn't send a second invitation.
    """

    key = connection.provider
    settings = settings_of(connection)
    own = _own_email(connection)
    if key == "google_calendar":
        base = f"{GOOGLE}/calendars/{_google_calendar_path(settings)}/events"
        body = _google_body(meeting, own=own, attendees=True, conference=conference)
        if remove_conference:
            body["conferenceData"] = None
        params = {"sendUpdates": "all" if invites and notify else "none", "conferenceDataVersion": 1}
        if event_id is None:
            body["id"] = aiva_event_id(meeting.id)
            try:
                event = (await core.request(db, connection, "POST", base, params=params, json=body)).json()
            except IntegrationError as exc:
                if exc.status_code != 400 or "exist" not in str(exc).lower():
                    raise
                # The ID is taken by an earlier copy (e.g. a deleted event): reuse it.
                body.pop("id", None)
                connection.last_error = None
                event = (await core.request(db, connection, "PATCH", f"{base}/{aiva_event_id(meeting.id)}", params=params, json=body)).json()
        else:
            event = (await core.request(db, connection, "PATCH", f"{base}/{quote(event_id, safe='')}", params=params, json=body)).json()
        result = _result(key, event, settings)
        status_code = ((event.get("conferenceData") or {}).get("createRequest") or {}).get("status", {}).get("statusCode")
        if conference == "google_meet" and not result["join_url"] and status_code == "pending":
            event = (await core.request(db, connection, "GET", f"{base}/{quote(result['event_id'], safe='')}")).json()
            result = _result(key, event, settings)
        return result
    body = _outlook_body(meeting, own=own, attendees=invites, conference=conference)
    if event_id is None:
        body["transactionId"] = str(meeting.id)
        event = (await core.request(db, connection, "POST", _graph_events_path(settings), json=body)).json()
    else:
        event = (await core.request(db, connection, "PATCH", f"{GRAPH}/events/{quote(event_id, safe='')}", json=body)).json()
    result = _result(key, event, settings)
    if conference == "microsoft_teams" and not result["join_url"] and result["event_id"]:
        event = (await core.request(db, connection, "GET", f"{GRAPH}/events/{quote(result['event_id'], safe='')}", params={"$select": "id,iCalUId,changeKey,lastModifiedDateTime,webLink,onlineMeeting,onlineMeetingUrl"})).json()
        result = {**result, **{k: v for k, v in _result(key, event, settings).items() if v}}
    return result


async def cancel_event(db: AsyncSession, connection: IntegrationConnection, link: dict[str, Any], *, notify: bool, reason: str | None) -> None:
    """Remove the meeting's event (sending cancellations when the calendar sent the invitations)."""

    event_id = link.get("event_id")
    if not event_id:
        return
    tell_guests = bool(link.get("invites")) and notify
    try:
        if connection.provider == "google_calendar":
            calendar_path = quote(link.get("calendar_id") or "primary", safe="")
            await core.request(
                db, connection, "DELETE", f"{GOOGLE}/calendars/{calendar_path}/events/{quote(event_id, safe='')}",
                params={"sendUpdates": "all" if tell_guests else "none"},
            )
        elif tell_guests:
            await core.request(db, connection, "POST", f"{GRAPH}/events/{quote(event_id, safe='')}/cancel", json={"comment": (reason or "")[:1000]})
        else:
            await core.request(db, connection, "DELETE", f"{GRAPH}/events/{quote(event_id, safe='')}")
    except IntegrationError as exc:
        if exc.status_code != 400:
            raise
        # Already gone (404 / 410) or not ours to cancel: nothing left to do.
        connection.last_error = None


def link_record(key: str, connection: IntegrationConnection, result: dict[str, Any], *, invites: bool, origin: str, organizer_self: bool = True) -> dict[str, Any]:
    return {
        "key": key,
        "user_id": str(connection.user_id) if connection.user_id else None,
        "calendar_id": result.get("calendar_id"),
        "event_id": result.get("event_id"),
        "ical_uid": result.get("ical_uid"),
        "etag": result.get("etag"),
        "updated": result.get("updated"),
        "html_link": result.get("html_link"),
        "invites": invites,
        "origin": origin,
        "organizer_self": organizer_self,
        "state": "synced",
        "error": None,
        "attempts": 0,
        "synced_at": utcnow().isoformat(),
    }


def mark_pending(link: dict[str, Any], error: str) -> dict[str, Any]:
    attempts = int(link.get("attempts") or 0) + 1
    out = {**link, "error": error[:500], "attempts": attempts}
    if attempts >= MAX_PUSH_ATTEMPTS:
        out["state"] = "error"
        out["next_attempt_at"] = None
    else:
        out["state"] = "pending"
        out["next_attempt_at"] = (utcnow() + timedelta(minutes=2 ** attempts)).isoformat()
    return out


async def link_connection(db: AsyncSession, meeting: Meeting, link: dict[str, Any]) -> IntegrationConnection:
    """The calendar connection a meeting's event belongs to (switched on and signed in)."""

    key = link.get("key")
    if key not in KEYS or not link.get("user_id"):
        raise IntegrationError("This meeting isn't linked to a calendar.", 409)
    return await core.active_connection(db, meeting.organization_id, key, uuid.UUID(link["user_id"]))


# ---------------------------------------------------------------------------
# Calendar → AIVA
# ---------------------------------------------------------------------------


@dataclass
class ExternalEvent:
    event_id: str
    status: str = "confirmed"  # confirmed | tentative | cancelled
    ical_uid: str | None = None
    title: str = ""
    description: str | None = None
    location: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    timezone: str | None = None
    attendees: list[dict[str, Any]] = field(default_factory=list)
    organizer_email: str | None = None
    organizer_self: bool = False
    self_response: str | None = None
    join_url: str | None = None
    updated: str | None = None
    etag: str | None = None
    html_link: str | None = None
    aiva_meeting_id: uuid.UUID | None = None
    busy: bool = True
    skip: bool = False


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        rest = tail[len(digits):]
        text = f"{head}.{digits[:6]}{rest}" if digits else f"{head}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _midnight(day: str, zone_name: str | None) -> datetime | None:
    try:
        value = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    return datetime.combine(value, time(0, 0), tzinfo=_zone(zone_name) or timezone.utc).astimezone(timezone.utc)


def google_event(item: dict[str, Any], calendar_zone: str | None) -> ExternalEvent:
    event = ExternalEvent(event_id=str(item.get("id") or ""), status=item.get("status") or "confirmed")
    if event.status == "cancelled" and "start" not in item:
        return event
    start, end = item.get("start") or {}, item.get("end") or {}
    event.all_day = "date" in start and "dateTime" not in start
    event.timezone = start.get("timeZone") or calendar_zone
    if event.all_day:
        event.start = _midnight(start.get("date"), event.timezone)
        event.end = _midnight(end.get("date") or start.get("date"), event.timezone)
        if event.start and (event.end is None or event.end <= event.start):
            event.end = event.start + timedelta(days=1)
    else:
        event.start = _parse(start.get("dateTime"))
        event.end = _parse(end.get("dateTime")) or (event.start + timedelta(hours=1) if event.start else None)
    event.ical_uid = item.get("iCalUID")
    event.title = (item.get("summary") or "(no title)")[:500]
    event.description = (item.get("description") or None) and item["description"][:10000]
    event.location = (item.get("location") or None) and item["location"][:500]
    for attendee in item.get("attendees") or []:
        address = (attendee.get("email") or "").strip().lower()
        if not address or attendee.get("resource"):
            continue
        response = GOOGLE_RESPONSES.get(attendee.get("responseStatus") or "", "needs_action")
        event.attendees.append({"email": address, "name": attendee.get("displayName") or address, "response": response})
        if attendee.get("self"):
            event.self_response = response
    organizer = item.get("organizer") or {}
    event.organizer_email = (organizer.get("email") or "").lower() or None
    event.organizer_self = bool(organizer.get("self"))
    event.join_url = _google_join(item)
    event.updated = item.get("updated")
    event.etag = item.get("etag")
    event.html_link = safe_url(item.get("htmlLink"))
    private = ((item.get("extendedProperties") or {}).get("private") or {}).get("aiva_meeting_id")
    try:
        event.aiva_meeting_id = uuid.UUID(private) if private else _meeting_from_event_id(event.event_id)
    except ValueError:
        event.aiva_meeting_id = _meeting_from_event_id(event.event_id)
    event.busy = item.get("transparency") != "transparent"
    event.skip = item.get("eventType") in SKIP_GOOGLE_TYPES
    return event


def outlook_event(item: dict[str, Any]) -> ExternalEvent:
    event = ExternalEvent(event_id=str(item.get("id") or ""))
    if "@removed" in item:
        event.status = "cancelled"
        return event
    event.status = "cancelled" if item.get("isCancelled") else "confirmed"
    event.all_day = bool(item.get("isAllDay"))
    start, end = item.get("start") or {}, item.get("end") or {}
    zone_name = start.get("timeZone") if _zone(start.get("timeZone")) else "UTC"
    event.timezone = item.get("originalStartTimeZone") if _zone(item.get("originalStartTimeZone")) else None

    def when(value: dict[str, Any]) -> datetime | None:
        moment = _parse(value.get("dateTime"))
        if moment is None:
            return None
        return moment.replace(tzinfo=_zone(zone_name) or timezone.utc).astimezone(timezone.utc) if moment.tzinfo == timezone.utc and zone_name != "UTC" else moment

    event.start = when(start)
    event.end = when(end) or (event.start + timedelta(hours=1) if event.start else None)
    event.ical_uid = item.get("iCalUId")
    event.title = (item.get("subject") or "(no title)")[:500]
    body = item.get("body") or {}
    text = body.get("content") if (body.get("contentType") or "").lower() == "text" else item.get("bodyPreview")
    event.description = (text or None) and text[:10000]
    event.location = ((item.get("location") or {}).get("displayName") or None)
    if event.location:
        event.location = event.location[:500]
    for attendee in item.get("attendees") or []:
        if attendee.get("type") == "resource":
            continue
        email_address = (attendee.get("emailAddress") or {})
        address = (email_address.get("address") or "").strip().lower()
        if not address:
            continue
        response = GRAPH_RESPONSES.get(((attendee.get("status") or {}).get("response")) or "none", "needs_action")
        event.attendees.append({"email": address, "name": email_address.get("name") or address, "response": response})
    organizer = ((item.get("organizer") or {}).get("emailAddress") or {})
    event.organizer_email = (organizer.get("address") or "").lower() or None
    event.organizer_self = bool(item.get("isOrganizer"))
    event.self_response = GRAPH_RESPONSES.get(((item.get("responseStatus") or {}).get("response")) or "none", "needs_action")
    event.join_url = safe_url((item.get("onlineMeeting") or {}).get("joinUrl")) or safe_url(item.get("onlineMeetingUrl"))
    event.updated = item.get("lastModifiedDateTime")
    event.etag = item.get("changeKey")
    event.html_link = safe_url(item.get("webLink"))
    event.busy = item.get("showAs") != "free"
    event.skip = item.get("type") == "seriesMaster"
    return event


@dataclass
class PullContext:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    key: str
    own_email: str
    settings: dict[str, Any]
    members: dict[str, tuple[str, str]] = field(default_factory=dict)  # email -> (user id, name)
    internal_domains: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=lambda: {"created": 0, "updated": 0, "cancelled": 0, "checked": 0})


async def _context(db: AsyncSession, connection: IntegrationConnection) -> PullContext:
    members = (
        await db.execute(
            select(User.id, User.email, User.full_name)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(OrganizationMembership.organization_id == connection.organization_id, OrganizationMembership.status == "active")
        )
    ).all()
    context = PullContext(
        organization_id=connection.organization_id,
        user_id=connection.user_id,  # type: ignore[arg-type]
        key=connection.provider,
        own_email=_own_email(connection),
        settings=settings_of(connection),
        members={(email or "").lower(): (str(user_id), name or email) for user_id, email, name in members if email},
    )
    context.internal_domains = {address.split("@")[-1] for address in [*context.members, context.own_email] if "@" in address}
    return context


async def _find_meeting(db: AsyncSession, context: PullContext, event: ExternalEvent) -> Meeting | None:
    org = context.organization_id
    if event.aiva_meeting_id:
        found = await db.get(Meeting, event.aiva_meeting_id)
        if found is not None and found.organization_id == org:
            return found
    if event.event_id:
        found = (
            await db.execute(
                select(Meeting).where(
                    Meeting.organization_id == org,
                    or_(
                        and_(Meeting.provider == context.key, Meeting.external_event_id == event.event_id[:500]),
                        and_(
                            Meeting.meeting_metadata["calendar"]["key"].astext == context.key,
                            Meeting.meeting_metadata["calendar"]["event_id"].astext == event.event_id,
                        ),
                    ),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    if event.ical_uid:
        query = select(Meeting).where(Meeting.organization_id == org, Meeting.external_event_id == event.ical_uid[:500])
        found = (await db.execute(query.limit(1))).scalar_one_or_none()
        if found is not None:
            return found
        if event.start is not None:
            found = (
                await db.execute(
                    select(Meeting).where(
                        Meeting.organization_id == org,
                        Meeting.meeting_metadata["calendar"]["ical_uid"].astext == event.ical_uid,
                        Meeting.start_at == event.start,
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if found is not None:
                return found
    return None


async def _typed_attendees(db: AsyncSession, context: PullContext, event: ExternalEvent) -> tuple[list[dict[str, Any]], list[Contact], list[Lead]]:
    emails = {a["email"] for a in event.attendees}
    if event.organizer_email:
        emails.add(event.organizer_email)
    emails.discard("")
    contacts: list[Contact] = []
    leads: list[Lead] = []
    if emails:
        contacts = list(
            (await db.execute(select(Contact).where(Contact.organization_id == context.organization_id, func.lower(Contact.email).in_(emails)))).scalars()
        )
        leads = list((await db.execute(select(Lead).where(Lead.organization_id == context.organization_id, func.lower(Lead.email).in_(emails)))).scalars())
    by_contact = {(c.email or "").lower(): c for c in contacts}
    by_lead = {(lead.email or "").lower(): lead for lead in leads}
    responses = {a["email"]: a for a in event.attendees}
    out: list[dict[str, Any]] = []
    ordered = list(dict.fromkeys(([event.organizer_email] if event.organizer_email else []) + [a["email"] for a in event.attendees]))
    for address in ordered:
        info = responses.get(address) or {"name": address, "response": "accepted" if address == event.organizer_email else "needs_action"}
        if address in context.members:
            member_id, member_name = context.members[address]
            entry = {"type": "user", "id": member_id, "email": address, "name": member_name}
        elif address in by_contact:
            contact = by_contact[address]
            entry = {"type": "contact", "id": str(contact.id), "email": address, "name": f"{contact.first_name or ''} {contact.last_name or ''}".strip() or address}
        elif address in by_lead:
            lead = by_lead[address]
            entry = {"type": "lead", "id": str(lead.id), "email": address, "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or address}
        else:
            entry = {"type": "email", "id": None, "email": address, "name": info.get("name") or address}
        entry["response"] = "accepted" if address == event.organizer_email and address not in responses else info.get("response") or "needs_action"
        out.append(entry)
    return out, contacts, leads


def _wanted(context: PullContext, event: ExternalEvent, contacts: list[Contact], leads: list[Lead]) -> bool:
    mode = context.settings["import_mode"]
    if mode == "all":
        return True
    if mode == "matched":
        return bool(contacts or leads)
    others = {a["email"] for a in event.attendees} | ({event.organizer_email} if event.organizer_email else set())
    others.discard(context.own_email)
    return any(address.split("@")[-1] not in context.internal_domains for address in others if "@" in address)


def _set_my_response(meeting: Meeting, context: PullContext, response: str | None) -> bool:
    if not response:
        return False
    me = str(context.user_id)
    attendees = [dict(a) for a in meeting.attendees or []]
    mine = next((a for a in attendees if a.get("type") == "user" and a.get("id") == me), None)
    if mine is None:
        name = next((n for e, (i, n) in context.members.items() if i == me), context.own_email)
        attendees.append({"type": "user", "id": me, "email": context.own_email or None, "name": name, "response": response})
    elif mine.get("response") == response:
        return False
    else:
        mine["response"] = response
    meeting.attendees = attendees
    return True


def _merge_responses(meeting: Meeting, event: ExternalEvent) -> None:
    responses = {a["email"]: a["response"] for a in event.attendees}
    changed = False
    attendees = []
    for attendee in meeting.attendees or []:
        attendee = dict(attendee)
        address = (attendee.get("email") or "").lower()
        if address in responses and attendee.get("response") != responses[address]:
            attendee["response"] = responses[address]
            changed = True
        attendees.append(attendee)
    if changed:
        meeting.attendees = attendees


async def apply_event(db: AsyncSession, context: PullContext, event: ExternalEvent) -> None:
    context.counts["checked"] += 1
    if not event.event_id:
        return
    meeting = await _find_meeting(db, context, event)
    link = dict((meeting.meeting_metadata or {}).get("calendar") or {}) if meeting is not None else {}
    owner = bool(meeting is not None and link.get("key") == context.key and link.get("user_id") == str(context.user_id))

    if event.status == "cancelled":
        if meeting is not None and owner and meeting.status == "scheduled":
            meeting.status = "cancelled"
            meeting.meeting_metadata = {
                **(meeting.meeting_metadata or {}),
                "cancel_reason": f"Cancelled in {LABEL[context.key]}",
                "calendar": {**link, "state": "synced", "updated": event.updated or link.get("updated")},
            }
            context.counts["cancelled"] += 1
        elif meeting is not None and not owner and meeting.status == "scheduled":
            # This person's copy of a teammate's meeting was removed from their calendar.
            _set_my_response(meeting, context, "declined")
        return
    if event.start is None or event.skip:
        return

    if meeting is not None:
        if not owner:
            # A teammate's meeting this person is invited to: record their reply only.
            _set_my_response(meeting, context, event.self_response)
            return
        if event.updated and event.updated == link.get("updated"):
            return  # unchanged since AIVA last pushed or read it
        if meeting.status == "completed":
            meeting.meeting_metadata = {**(meeting.meeting_metadata or {}), "calendar": {**link, "updated": event.updated}}
            return
        pulled = meeting.provider in KEYS
        meeting.title = event.title or meeting.title
        meeting.start_at = event.start
        meeting.end_at = event.end or event.start + timedelta(hours=1)
        meeting.is_all_day = event.all_day
        if pulled:
            meeting.description = event.description
            meeting.location = event.location
            if event.join_url:
                meeting.meeting_url = event.join_url[:1000]
                meeting.is_online = True
        elif event.join_url and not meeting.meeting_url:
            meeting.meeting_url = event.join_url[:1000]
        _merge_responses(meeting, event)
        metadata = dict(meeting.meeting_metadata or {})
        if meeting.status == "cancelled" and pulled:
            meeting.status = "scheduled"
            metadata.pop("cancel_reason", None)
        metadata.pop("reminded_at", None)
        metadata["calendar"] = {**link, "updated": event.updated, "etag": event.etag, "html_link": event.html_link or link.get("html_link"), "state": "synced", "error": None}
        meeting.meeting_metadata = metadata
        context.counts["updated"] += 1
        return

    # Something new in the calendar.
    if not context.settings["pull_events"] or event.self_response == "declined" or not event.busy:
        return
    now = utcnow()
    if event.start < now - timedelta(days=PAST_DAYS) or event.start > now + timedelta(days=FUTURE_DAYS):
        return
    attendees, contacts, leads = await _typed_attendees(db, context, event)
    if not _wanted(context, event, contacts, leads):
        return
    contact = contacts[0] if contacts else None
    lead = leads[0] if leads and contact is None else None
    meeting = Meeting(
        id=uuid.uuid4(),
        organization_id=context.organization_id,
        provider=context.key,
        external_event_id=event.event_id[:500],
        organizer_user_id=context.user_id,
        title=event.title or "(no title)",
        description=event.description,
        location=event.location,
        meeting_url=(event.join_url or None) and event.join_url[:1000],
        is_online=bool(event.join_url),
        start_at=event.start,
        end_at=event.end or event.start + timedelta(hours=1),
        timezone=event.timezone if _zone(event.timezone) else None,
        status="scheduled",
        attendees=attendees,
        is_all_day=event.all_day,
        action_items=[],
        contact_id=contact.id if contact else None,
        account_id=contact.account_id if contact else None,
        lead_id=lead.id if lead else None,
        meeting_metadata={
            "reminder_minutes": None,
            "sequence": 0,
            "organizer_email": event.organizer_email,
            "calendar": {
                "key": context.key,
                "user_id": str(context.user_id),
                "calendar_id": context.settings.get("calendar_id") or ("primary" if context.key == "google_calendar" else None),
                "event_id": event.event_id,
                "ical_uid": event.ical_uid,
                "etag": event.etag,
                "updated": event.updated,
                "html_link": event.html_link,
                "invites": True,
                "origin": "pulled",
                "organizer_self": event.organizer_self,
                "state": "synced",
                "error": None,
                "attempts": 0,
                "synced_at": now.isoformat(),
            },
        },
    )
    db.add(meeting)
    await db.flush()
    context.counts["created"] += 1
    external = {a["email"] for a in attendees if a.get("type") != "user" and a.get("email")}
    if external and event.start > now:
        from app.services import outbound

        await outbound.meeting_booked(db, context.organization_id, external)


async def _pull_google(db: AsyncSession, connection: IntegrationConnection, context: PullContext, retried: bool = False) -> None:
    calendar_id = context.settings.get("calendar_id") or "primary"
    sync = dict((connection.config or {}).get("sync") or {})
    if sync.get("calendar_id") != calendar_id:
        sync = {"calendar_id": calendar_id}
    url = f"{GOOGLE}/calendars/{quote(calendar_id, safe='')}/events"
    params: dict[str, Any] = {"singleEvents": "true", "maxResults": 250, "showDeleted": "true"}
    if sync.get("page_token"):
        params.update(sync.get("page_params") or {})
        params["pageToken"] = sync["page_token"]
    elif sync.get("token"):
        params["syncToken"] = sync["token"]
    else:
        params["timeMin"] = _iso(utcnow() - timedelta(days=PAST_DAYS))
    page_params = {k: v for k, v in params.items() if k in {"syncToken", "timeMin"}}
    incremental = "syncToken" in params or "pageToken" in params
    next_page: str | None = None
    try:
        for _ in range(MAX_PAGES):
            data = (await core.request(db, connection, "GET", url, params=params)).json()
            zone = data.get("timeZone")
            for item in data.get("items") or []:
                await apply_event(db, context, google_event(item, zone))
            next_page = data.get("nextPageToken")
            if not next_page:
                sync = {"calendar_id": calendar_id, "token": data.get("nextSyncToken")}
                break
            params["pageToken"] = next_page
        else:
            sync = {"calendar_id": calendar_id, "page_token": next_page, "page_params": page_params}
    except IntegrationError as exc:
        if exc.status_code != 400 or not incremental or retried:
            raise
        # The sync token expired (410) or no longer matches: full sync again.
        connection.config = {**(connection.config or {}), "sync": {"calendar_id": calendar_id}}
        connection.last_error = None
        await _pull_google(db, connection, context, retried=True)
        return
    connection.config = {**(connection.config or {}), "sync": sync}


async def _pull_outlook(db: AsyncSession, connection: IntegrationConnection, context: PullContext, retried: bool = False) -> None:
    calendar_id = context.settings.get("calendar_id")
    sync = dict((connection.config or {}).get("sync") or {})
    now = utcnow()
    window_end = _parse(sync.get("window_end"))
    fresh = sync.get("calendar_id") != calendar_id or not sync.get("link") or window_end is None or window_end - now < timedelta(days=30)
    params: dict[str, Any] | None = None
    if fresh:
        base = f"{GRAPH}/calendars/{quote(calendar_id, safe='')}/calendarView/delta" if calendar_id else f"{GRAPH}/calendarView/delta"
        end = now + timedelta(days=FUTURE_DAYS)
        url = base
        params = {"startDateTime": _iso(now - timedelta(days=PAST_DAYS)), "endDateTime": _iso(end)}
        sync = {"calendar_id": calendar_id, "window_end": _iso(end)}
    else:
        url = sync["link"]
    headers = {"Prefer": 'odata.maxpagesize=100, outlook.timezone="UTC", outlook.body-content-type="text"'}
    try:
        for _ in range(MAX_PAGES):
            data = (await core.request(db, connection, "GET", url, params=params, headers=headers)).json()
            params = None
            for item in data.get("value") or []:
                await apply_event(db, context, outlook_event(item))
            if data.get("@odata.deltaLink"):
                sync["link"] = data["@odata.deltaLink"]
                break
            if not data.get("@odata.nextLink"):
                break
            url = data["@odata.nextLink"]
            sync["link"] = url
    except IntegrationError as exc:
        if exc.status_code != 400 or fresh or retried:
            raise
        connection.config = {**(connection.config or {}), "sync": {"calendar_id": calendar_id}}
        connection.last_error = None
        await _pull_outlook(db, connection, context, retried=True)
        return
    connection.config = {**(connection.config or {}), "sync": sync}


async def pull(db: AsyncSession, connection: IntegrationConnection) -> dict[str, int]:
    """Read what changed in the person's calendar. Doesn't commit."""

    context = await _context(db, connection)
    previous = db.sync_session.info.get("automation_disabled", False)
    db.sync_session.info["automation_disabled"] = True
    try:
        if connection.provider == "google_calendar":
            await _pull_google(db, connection, context)
        else:
            await _pull_outlook(db, connection, context)
    finally:
        db.sync_session.info["automation_disabled"] = previous
    return context.counts


async def sync_connection(db: AsyncSession, connection: IntegrationConnection) -> dict[str, int]:
    """Pull (and record the outcome) for one connection. Doesn't commit."""

    key = connection.provider
    now = utcnow()
    try:
        counts = await pull(db, connection)
    except IntegrationError as exc:
        changed = connection.last_error != str(exc)
        connection.last_error = str(exc)[:1000]
        connection.last_sync_at = now
        if changed:
            core.log(db, connection.organization_id, key, "sync_failed", f"Couldn't read the calendar: {exc}", level="error", connection=connection)
        raise
    connection.last_sync_at = now
    connection.last_error = None
    stats = dict((connection.config or {}).get("sync_stats") or {})
    for name in ("created", "updated", "cancelled"):
        stats[f"{name}_total"] = int(stats.get(f"{name}_total", 0)) + counts[name]
    stats["last"] = counts
    connection.config = {**(connection.config or {}), "sync_stats": stats}
    if counts["created"] or counts["updated"] or counts["cancelled"]:
        parts = [f"{counts[n]} {n}" for n in ("created", "updated", "cancelled") if counts[n]]
        core.log(db, connection.organization_id, key, "calendar_synced", f"Calendar sync: {', '.join(parts)}.", level="success", connection=connection, detail=counts)
    return counts


# ---------------------------------------------------------------------------
# Retrying pushes that didn't go through
# ---------------------------------------------------------------------------


async def retry_push(db: AsyncSession, meeting: Meeting) -> bool:
    """Push a meeting whose calendar update is pending. Returns True when it's in sync now."""

    metadata = dict(meeting.meeting_metadata or {})
    link = dict(metadata.get("calendar") or {})
    if not link:
        return True
    try:
        connection = await link_connection(db, meeting, link)
        if meeting.status == "cancelled":
            await cancel_event(db, connection, link, notify=True, reason=metadata.get("cancel_reason"))
            link = {**link, "state": "synced", "error": None, "attempts": 0, "next_attempt_at": None}
        elif meeting.status == "completed" and not link.get("event_id"):
            link = {**link, "state": "synced", "error": None, "next_attempt_at": None}
        else:
            result = await push_event(db, connection, meeting, event_id=link.get("event_id"), invites=bool(link.get("invites")), notify=False)
            link = {**link, **link_record(link["key"], connection, result, invites=bool(link.get("invites")), origin=link.get("origin") or "aiva", organizer_self=link.get("organizer_self", True))}
            if meeting.provider == "aiva" and result.get("ical_uid") and not meeting.external_event_id:
                meeting.external_event_id = result["ical_uid"][:500]
    except IntegrationError as exc:
        link = mark_pending(link, str(exc))
        metadata["calendar"] = link
        meeting.meeting_metadata = metadata
        if link["state"] == "error":
            core.log(db, meeting.organization_id, link.get("key") or "google_calendar", "push_failed", f"“{meeting.title}” couldn't be updated in the calendar: {exc}", level="error")
        return False
    metadata["calendar"] = link
    meeting.meeting_metadata = metadata
    return True


async def _retry_pending(db: AsyncSession) -> int:
    now = utcnow()
    meetings = (
        await db.execute(
            select(Meeting).where(Meeting.meeting_metadata["calendar"]["state"].astext == "pending").order_by(Meeting.updated_at).limit(25)
        )
    ).scalars().all()
    done = 0
    for meeting in meetings:
        due = _parse(((meeting.meeting_metadata or {}).get("calendar") or {}).get("next_attempt_at"))
        if due is not None and due > now:
            continue
        if await retry_push(db, meeting):
            done += 1
        await db.commit()
    return done


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if key not in KEYS:
        return
    config = dict(connection.config or {})
    for name, value in DEFAULTS.items():
        config.setdefault(name, value)
    config["sync"] = {}
    connection.config = config
    connection.last_sync_at = None  # the scheduler picks it up within a minute
    core.log(db, connection.organization_id, key, "calendar_ready", f"{LABEL[key]} {connection.account_label or ''} will sync with AIVA meetings.".replace("  ", " "), level="success", connection=connection)


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    """Meetings stay in AIVA; pending pushes to this calendar stop."""

    if key not in KEYS or connection.user_id is None:
        return
    meetings = (
        await db.execute(
            select(Meeting).where(
                Meeting.organization_id == connection.organization_id,
                Meeting.meeting_metadata["calendar"]["key"].astext == key,
                Meeting.meeting_metadata["calendar"]["user_id"].astext == str(connection.user_id),
                Meeting.meeting_metadata["calendar"]["state"].astext == "pending",
            )
        )
    ).scalars().all()
    for meeting in meetings:
        link = dict((meeting.meeting_metadata or {}).get("calendar") or {})
        meeting.meeting_metadata = {**(meeting.meeting_metadata or {}), "calendar": {**link, "state": "error", "error": f"{LABEL[key]} was disconnected.", "next_attempt_at": None}}


async def _google_summary(db: AsyncSession, connection: IntegrationConnection, settings: dict[str, Any]) -> dict[str, Any]:
    """The calendar's name and time zone, read through events.list (allowed by the calendar.events scope)."""

    return (
        await core.request(
            db, connection, "GET", f"{GOOGLE}/calendars/{_google_calendar_path(settings)}/events",
            params={"maxResults": 1, "singleEvents": "true", "timeMin": _iso(utcnow())},
        )
    ).json()


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    settings = settings_of(connection)
    if key in {"google_calendar", "google_meet"}:
        summary = await _google_summary(db, connection, settings)
        name = summary.get("summary") or "your calendar"
        if key == "google_meet":
            try:
                calendar = (await core.request(db, connection, "GET", f"{GOOGLE}/calendars/{_google_calendar_path(settings)}")).json()
            except IntegrationError:
                connection.last_error = None  # needs a broader scope; the events check above is enough
                calendar = {}
            allowed = (calendar.get("conferenceProperties") or {}).get("allowedConferenceSolutionTypes")
            if allowed is not None and "hangoutsMeet" not in allowed:
                raise IntegrationError("This Google calendar doesn't allow Google Meet links. Your Google Workspace admin may have turned Meet off.", 400)
            return f"Google Meet links will be created through {name}."
        return f"Connected to {name} ({summary.get('timeZone') or 'UTC'}); AIVA can read and add events."
    path = f"{GRAPH}/calendars/{quote(settings['calendar_id'], safe='')}" if settings.get("calendar_id") else f"{GRAPH}/calendar"
    calendar = (await core.request(db, connection, "GET", path, params={"$select": "name,canEdit,owner"})).json()
    if calendar.get("canEdit") is False:
        raise IntegrationError(f"AIVA can read {calendar.get('name') or 'that calendar'} but can't add meetings to it. Pick a calendar you own.", 400)
    return f"Connected to {calendar.get('name') or 'your calendar'}; AIVA can read and add events."


async def list_calendars(db: AsyncSession, connection: IntegrationConnection) -> list[dict[str, Any]]:
    if connection.provider == "google_calendar":
        try:
            data = (await core.request(db, connection, "GET", f"{GOOGLE}/users/me/calendarList", params={"minAccessRole": "writer", "maxResults": 100})).json()
        except IntegrationError:
            # The calendar.events scope can't list calendars; offer the primary one and a typed ID.
            connection.last_error = None
            return [{"id": None, "name": "Primary calendar", "primary": True}]
        items = [{"id": None if c.get("primary") else c.get("id"), "name": c.get("summaryOverride") or c.get("summary") or c.get("id"), "primary": bool(c.get("primary"))} for c in data.get("items") or []]
        return sorted(items, key=lambda c: (not c["primary"], (c["name"] or "").lower()))
    data = (await core.request(db, connection, "GET", f"{GRAPH}/calendars", params={"$select": "id,name,canEdit,isDefaultCalendar", "$top": 100})).json()
    items = [
        {"id": None if c.get("isDefaultCalendar") else c.get("id"), "name": c.get("name"), "primary": bool(c.get("isDefaultCalendar"))}
        for c in data.get("value") or []
        if c.get("canEdit", True)
    ]
    return sorted(items, key=lambda c: (not c["primary"], (c["name"] or "").lower()))


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    if key == "google_meet":
        connection = await core.connection_for(db, org, "google_calendar", principal.user_id)
        upcoming = await _count(db, org, principal.user_id, conference="google_meet")
        return {
            "calendar_connected": bool(connection and connection.status != "needs_reauth"),
            "calendar_account": connection.account_label if connection else None,
            "upcoming_with_link": upcoming,
        }
    connection = await core.connection_for(db, org, key, principal.user_id)
    if connection is None:
        return {"settings": dict(DEFAULTS), "connected": False, "import_modes": list(IMPORT_MODES)}
    problems = (
        await db.execute(
            select(Meeting.id, Meeting.title, Meeting.start_at, Meeting.meeting_metadata)
            .where(
                Meeting.organization_id == org,
                Meeting.meeting_metadata["calendar"]["key"].astext == key,
                Meeting.meeting_metadata["calendar"]["user_id"].astext == str(principal.user_id),
                Meeting.meeting_metadata["calendar"]["state"].astext.in_(("pending", "error")),
            )
            .order_by(Meeting.start_at.desc())
            .limit(10)
        )
    ).all()
    synced = (
        await db.execute(
            select(func.count()).select_from(Meeting).where(
                Meeting.organization_id == org,
                Meeting.meeting_metadata["calendar"]["key"].astext == key,
                Meeting.meeting_metadata["calendar"]["user_id"].astext == str(principal.user_id),
                Meeting.start_at >= utcnow(),
            )
        )
    ).scalar_one()
    return {
        "connected": True,
        "settings": settings_of(connection),
        "import_modes": list(IMPORT_MODES),
        "sync_every_minutes": int(SYNC_EVERY.total_seconds() // 60),
        "window": {"past_days": PAST_DAYS, "future_days": FUTURE_DAYS},
        "stats": {**((connection.config or {}).get("sync_stats") or {}), "upcoming_linked": int(synced)},
        "problems": [
            {
                "id": str(meeting_id),
                "title": title,
                "start_at": start_at,
                "state": ((metadata or {}).get("calendar") or {}).get("state"),
                "error": ((metadata or {}).get("calendar") or {}).get("error"),
            }
            for meeting_id, title, start_at, metadata in problems
        ],
    }


async def _count(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID, *, conference: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(Meeting).where(
                    Meeting.organization_id == organization_id,
                    Meeting.organizer_user_id == user_id,
                    Meeting.status == "scheduled",
                    Meeting.start_at >= utcnow(),
                    Meeting.meeting_metadata["conference"]["type"].astext == conference,
                )
            )
        ).scalar_one()
    )


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if not personal or key not in KEYS:
        return {}
    cleaned: dict[str, Any] = {}
    for name in ("push_meetings", "invites_from_calendar", "pull_events"):
        if name in config:
            cleaned[name] = bool(config[name])
    if "import_mode" in config:
        if config["import_mode"] not in IMPORT_MODES:
            raise IntegrationError("Choose which events to bring in: matched, external or all.", 422)
        cleaned["import_mode"] = config["import_mode"]
    if "calendar_id" in config:
        value = str(config.get("calendar_id") or "").strip()
        if len(value) > 300:
            raise IntegrationError("That calendar ID is too long.", 422)
        value = None if value in {"", "primary"} else value
        connection = await core.connection_for(db, principal.organization_id, key, principal.user_id)
        if connection is not None and settings_of(connection).get("calendar_id") != value:
            cleaned["sync"] = {}  # a different calendar: read it from the start
        cleaned["calendar_id"] = value
    return cleaned


async def run_due(db: AsyncSession) -> int:
    """Pull changes for calendars that are due, then retry pushes that didn't go through."""

    cutoff = utcnow() - SYNC_EVERY
    connections = (
        await db.execute(
            select(IntegrationConnection)
            .join(
                IntegrationToggle,
                and_(
                    IntegrationToggle.organization_id == IntegrationConnection.organization_id,
                    IntegrationToggle.provider == IntegrationConnection.provider,
                    IntegrationToggle.enabled.is_(True),
                ),
            )
            .where(
                IntegrationConnection.provider.in_(KEYS),
                IntegrationConnection.user_id.is_not(None),
                IntegrationConnection.status == "connected",
                or_(IntegrationConnection.last_sync_at.is_(None), IntegrationConnection.last_sync_at <= cutoff),
            )
            .order_by(IntegrationConnection.last_sync_at.asc().nulls_first())
            .limit(JOB_BATCH)
        )
    ).scalars().all()
    handled = 0
    for connection in connections:
        try:
            await sync_connection(db, connection)
            handled += 1
        except IntegrationError as exc:
            logger.info("Calendar sync for %s failed: %s", connection.account_label, exc)
        except Exception:  # noqa: BLE001 - log it; the next run carries on with the others
            logger.exception("Calendar sync crashed for connection %s", connection.id)
            await db.rollback()  # expires the loaded rows, so stop this run here
            return handled
        await db.commit()
    return handled + await _retry_pending(db)
