"""Zoom and Microsoft Teams, plus the online-meeting links on AIVA meetings.

* **Zoom** (each person signs in): picking Zoom when scheduling creates a Zoom
  meeting under the organizer's account (``POST /users/me/meetings``) and puts
  the join link on the AIVA meeting; rescheduling updates it and cancelling
  deletes it.
* **Microsoft Teams** (an admin pastes a channel webhook): CRM alerts and
  workflow posts go to the channel. This is the same delivery as the older
  Teams connection (``Integration`` kind ``teams`` + ``services.chat``), which the
  hub keeps in step: connecting, switching off or disconnecting here updates it.
  Teams *meeting links* are created through each person's Outlook Calendar
  connection (a calendar event with ``isOnlineMeeting``), so they need Outlook
  Calendar connected and a Microsoft 365 work or school account.

The meetings API calls ``after_create`` / ``after_update`` / ``after_cancel`` /
``before_delete`` so the conference link and the calendar event follow the
meeting. They never raise for provider problems: the meeting is saved and the
problem comes back as a warning the person sees (and pending calendar pushes
are retried by the scheduler).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.integrations import core
from app.integrations.catalog import INTEGRATIONS
from app.integrations.core import IntegrationError
from app.integrations.providers import calendars
from app.models.editions import Integration, OutboundMessage
from app.models.integration_hub import IntegrationConnection, IntegrationToggle
from app.models.meeting import Meeting

logger = logging.getLogger("aiva.integrations.meetings")

ZOOM = "https://api.zoom.us/v2"

CONFERENCES: dict[str, str] = {"google_meet": "Google Meet", "zoom": "Zoom", "microsoft_teams": "Microsoft Teams"}
CALENDAR_FOR_CONFERENCE = {"google_meet": "google_calendar", "microsoft_teams": "outlook_calendar"}
ZOOM_DEFAULTS: dict[str, Any] = {"waiting_room": True, "join_before_host": False, "mute_upon_entry": True}
TEAMS_DEFAULT_EVENTS = ["deal_won", "case_urgent"]
TEAMS_HOSTS = (".webhook.office.com", ".logic.azure.com", ".powerplatform.com", ".powerautomate.com")


class _Unset:
    pass


UNSET: Any = _Unset()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Zoom
# ---------------------------------------------------------------------------


def zoom_settings(connection: IntegrationConnection | None) -> dict[str, Any]:
    config = dict(connection.config or {}) if connection else {}
    return {name: bool(config.get(name, value)) for name, value in ZOOM_DEFAULTS.items()}


def _zoom_body(meeting: Meeting, settings: dict[str, Any] | None) -> dict[str, Any]:
    end = meeting.end_at or meeting.start_at + timedelta(hours=1)
    minutes = max(1, int((end - meeting.start_at).total_seconds() // 60))
    body: dict[str, Any] = {
        "topic": meeting.title[:200],
        "type": 2,  # scheduled
        "start_time": meeting.start_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": minutes,
        "timezone": meeting.timezone if calendars._zone(meeting.timezone) else "UTC",
        "agenda": (meeting.description or "")[:2000],
    }
    if settings is not None:
        body["settings"] = {**settings, "approval_type": 2}  # no registration
    return body


async def zoom_create(db: AsyncSession, meeting: Meeting) -> dict[str, Any]:
    connection = await core.active_connection(db, meeting.organization_id, "zoom", meeting.organizer_user_id)
    created = (await core.request(db, connection, "POST", f"{ZOOM}/users/me/meetings", json=_zoom_body(meeting, zoom_settings(connection)))).json()
    join_url = calendars.safe_url(created.get("join_url"))
    if not join_url:
        raise IntegrationError("Zoom didn't return a join link.", 502)
    core.log(db, meeting.organization_id, "zoom", "meeting_created", f"Created a Zoom meeting for “{meeting.title}”.", connection=connection)
    return {
        "type": "zoom",
        "id": str(created.get("id")),
        "join_url": join_url,
        "user_id": str(meeting.organizer_user_id) if meeting.organizer_user_id else None,
        "account": connection.account_label,
    }


async def zoom_update(db: AsyncSession, meeting: Meeting, conference: dict[str, Any]) -> None:
    connection = await core.active_connection(db, meeting.organization_id, "zoom", uuid.UUID(conference["user_id"]) if conference.get("user_id") else meeting.organizer_user_id)
    await core.request(db, connection, "PATCH", f"{ZOOM}/meetings/{conference['id']}", json=_zoom_body(meeting, None))


async def zoom_delete(db: AsyncSession, meeting: Meeting, conference: dict[str, Any]) -> None:
    connection = await core.active_connection(db, meeting.organization_id, "zoom", uuid.UUID(conference["user_id"]) if conference.get("user_id") else meeting.organizer_user_id)
    try:
        await core.request(db, connection, "DELETE", f"{ZOOM}/meetings/{conference['id']}", params={"schedule_for_reminder": "false"})
    except IntegrationError as exc:
        if exc.status_code != 400:
            raise
        connection.last_error = None  # already deleted in Zoom


# ---------------------------------------------------------------------------
# Meeting lifecycle (called by the meetings API)
# ---------------------------------------------------------------------------


def _has_guests(meeting: Meeting, own: str | None) -> bool:
    return any((a.get("email") or "").lower() not in {"", (own or "").lower()} for a in meeting.attendees or [])


def conference_out(meeting: Meeting) -> dict[str, Any] | None:
    conference = (meeting.meeting_metadata or {}).get("conference")
    if not conference or not conference.get("type"):
        return None
    return {"type": conference["type"], "label": CONFERENCES.get(conference["type"], conference["type"]), "join_url": conference.get("join_url")}


def calendar_out(meeting: Meeting) -> dict[str, Any] | None:
    link = (meeting.meeting_metadata or {}).get("calendar")
    if not link or not link.get("key"):
        return None
    return {
        "key": link["key"],
        "label": calendars.LABEL.get(link["key"], link["key"]),
        "state": link.get("state"),
        "error": link.get("error"),
        "origin": link.get("origin"),
        "html_link": link.get("html_link"),
        "invites": bool(link.get("invites")),
        "user_id": link.get("user_id"),
        "organizer_self": link.get("organizer_self", True),
    }


async def _calendar_target(db: AsyncSession, meeting: Meeting, conference: str | None) -> tuple[str, IntegrationConnection, dict[str, Any]] | None:
    org, organizer = meeting.organization_id, meeting.organizer_user_id
    if conference in CALENDAR_FOR_CONFERENCE:
        await core.require_enabled(db, org, conference)
        return await calendars.require_target(db, org, CALENDAR_FOR_CONFERENCE[conference], organizer)
    return await calendars.default_target(db, org, organizer)


async def _add_to_calendar(
    db: AsyncSession, meeting: Meeting, metadata: dict[str, Any], *, conference: str | None, invites_allowed: bool, notify: bool, warnings: list[str]
) -> bool:
    """Create the meeting's calendar event (with a Meet / Teams link when asked). Returns True when the calendar sends the invitations."""

    try:
        target = await _calendar_target(db, meeting, conference)
    except IntegrationError as exc:
        warnings.append(f"Couldn't add a {CONFERENCES[conference]} link: {exc}" if conference else str(exc))
        target = await calendars.default_target(db, meeting.organization_id, meeting.organizer_user_id) if conference else None
        conference = None
    if target is None:
        return False
    key, connection, settings = target
    invites = bool(settings["invites_from_calendar"] and invites_allowed and _has_guests(meeting, connection.account_label))
    try:
        result = await calendars.push_event(db, connection, meeting, event_id=None, invites=invites, notify=notify, conference=conference)
    except IntegrationError as exc:
        metadata["calendar"] = calendars.mark_pending(
            {"key": key, "user_id": str(connection.user_id), "invites": False, "origin": "aiva", "organizer_self": True, "attempts": 0}, str(exc)
        )
        if conference:
            warnings.append(f"Couldn't add a {CONFERENCES[conference]} link: {exc}")
        warnings.append(f"The meeting isn't in your {calendars.LABEL[key]} yet ({exc}). AIVA will keep trying.")
        return False
    metadata["calendar"] = calendars.link_record(key, connection, result, invites=invites, origin="aiva")
    if result.get("ical_uid"):
        meeting.external_event_id = result["ical_uid"][:500]
    if conference:
        if result.get("join_url"):
            meeting.meeting_url = result["join_url"][:1000]
            meeting.is_online = True
            metadata["conference"] = {"type": conference, "id": result.get("event_id"), "join_url": result["join_url"], "user_id": str(connection.user_id), "account": connection.account_label}
        else:
            hint = " Teams links need a Microsoft 365 work or school account." if conference == "microsoft_teams" else ""
            warnings.append(f"{calendars.LABEL[key]} didn't return a {CONFERENCES[conference]} link.{hint}")
    core.log(db, meeting.organization_id, key, "event_created", f"Added “{meeting.title}” to {connection.account_label or 'the calendar'}.", connection=connection)
    return invites


async def after_create(db: AsyncSession, meeting: Meeting, *, conference: str | None, send_invites: bool) -> dict[str, Any]:
    """Create the conference link and calendar event for a new AIVA meeting.

    Returns ``warnings`` (shown to the person) and ``calendar_invites`` (the
    calendar sent the invitations, so AIVA shouldn't email its own).
    """

    warnings: list[str] = []
    metadata = dict(meeting.meeting_metadata or {})
    conference = conference if conference in CONFERENCES else None
    if conference == "zoom":
        try:
            metadata["conference"] = await zoom_create(db, meeting)
            meeting.meeting_url = metadata["conference"]["join_url"][:1000]
            meeting.is_online = True
        except IntegrationError as exc:
            warnings.append(f"Couldn't create the Zoom meeting: {exc}")
        conference = None
    meeting.meeting_metadata = metadata
    invites = await _add_to_calendar(db, meeting, metadata, conference=conference, invites_allowed=send_invites, notify=send_invites, warnings=warnings)
    meeting.meeting_metadata = metadata
    return {"warnings": warnings, "calendar_invites": invites}


async def after_update(
    db: AsyncSession,
    meeting: Meeting,
    *,
    conference: Any = UNSET,
    content_changed: bool,
    send_updates: bool,
) -> dict[str, Any]:
    """Follow a meeting edit: switch or update the conference and update the calendar event.

    ``calendar_invites``: the calendar owns the invitations, so AIVA shouldn't
    email updates itself. ``link_changed``: the join link changed.
    """

    warnings: list[str] = []
    metadata = dict(meeting.meeting_metadata or {})
    current_conf = dict(metadata.get("conference") or {})
    current = current_conf.get("type")
    desired = current if conference is UNSET else (conference if conference in CONFERENCES else None)
    link_changed = False
    pulled = meeting.provider in calendars.KEYS
    add_conference: str | None = None
    remove_conference = False

    if desired != current:
        if current == "zoom" and current_conf.get("id"):
            try:
                await zoom_delete(db, meeting, current_conf)
            except IntegrationError as exc:
                warnings.append(f"The old Zoom meeting couldn't be deleted: {exc}")
        if current == "google_meet":
            remove_conference = True
        elif current == "microsoft_teams":
            warnings.append("The Teams link stays on the Outlook event; it's no longer on the AIVA meeting.")
        if current and current_conf.get("join_url") and meeting.meeting_url == current_conf.get("join_url"):
            meeting.meeting_url = None
            link_changed = True
        metadata.pop("conference", None)
        if desired == "zoom":
            try:
                metadata["conference"] = await zoom_create(db, meeting)
                meeting.meeting_url = metadata["conference"]["join_url"][:1000]
                meeting.is_online = True
                link_changed = True
            except IntegrationError as exc:
                warnings.append(f"Couldn't create the Zoom meeting: {exc}")
        elif desired in CALENDAR_FOR_CONFERENCE:
            add_conference = desired
    elif current == "zoom" and content_changed and current_conf.get("id"):
        try:
            await zoom_update(db, meeting, current_conf)
        except IntegrationError as exc:
            warnings.append(f"Zoom wasn't updated: {exc}")
    meeting.meeting_metadata = metadata

    link = dict(metadata.get("calendar") or {})
    calendar_invites = bool(link.get("invites")) or pulled
    if link.get("event_id"):
        # Also a natural retry when an earlier update gave up.
        can_push = not pulled or link.get("organizer_self")
        wants_conference_here = add_conference is None or CALENDAR_FOR_CONFERENCE[add_conference] == link.get("key")
        if add_conference and not wants_conference_here:
            warnings.append(f"{CONFERENCES[add_conference]} links come from {calendars.LABEL[CALENDAR_FOR_CONFERENCE[add_conference]]}, but this meeting is in {calendars.LABEL.get(link.get('key'), 'another calendar')}.")
            add_conference = None
        if can_push and (content_changed or add_conference or remove_conference):
            try:
                if add_conference:
                    await core.require_enabled(db, meeting.organization_id, add_conference)
                connection = await calendars.link_connection(db, meeting, link)
                result = await calendars.push_event(
                    db, connection, meeting, event_id=link["event_id"], invites=bool(link.get("invites")), notify=send_updates,
                    conference=add_conference, remove_conference=remove_conference,
                )
                metadata["calendar"] = {**link, **{k: v for k, v in calendars.link_record(link["key"], connection, result, invites=bool(link.get("invites")), origin=link.get("origin") or "aiva", organizer_self=link.get("organizer_self", True)).items() if k not in {"origin", "organizer_self"}}}
                if add_conference and result.get("join_url"):
                    meeting.meeting_url = result["join_url"][:1000]
                    meeting.is_online = True
                    metadata["conference"] = {"type": add_conference, "id": result.get("event_id"), "join_url": result["join_url"], "user_id": link.get("user_id"), "account": connection.account_label}
                    link_changed = True
                elif add_conference:
                    warnings.append(f"The calendar didn't return a {CONFERENCES[add_conference]} link.")
            except IntegrationError as exc:
                metadata["calendar"] = calendars.mark_pending(link, str(exc))
                warnings.append(f"Your calendar wasn't updated yet ({exc}). AIVA will keep trying.")
                if add_conference:
                    warnings.append(f"Couldn't add a {CONFERENCES[add_conference]} link: {exc}")
                calendar_invites = False
    elif link.get("state") == "pending":
        pass  # the scheduler creates it with the latest details
    elif not pulled and (content_changed or add_conference) and meeting.start_at > utcnow() - timedelta(days=1):
        # Not in a calendar yet (scheduled before the calendar was connected, or the
        # last attempt gave up): add it now. Guests already have AIVA's invitation.
        calendar_invites = await _add_to_calendar(db, meeting, metadata, conference=add_conference, invites_allowed=False, notify=False, warnings=warnings)
        if add_conference and metadata.get("conference", {}).get("type") == add_conference:
            link_changed = True
    meeting.meeting_metadata = metadata
    return {"warnings": warnings, "calendar_invites": calendar_invites, "link_changed": link_changed}


async def after_cancel(db: AsyncSession, meeting: Meeting, *, reason: str | None, notify: bool) -> dict[str, Any]:
    warnings: list[str] = []
    metadata = dict(meeting.meeting_metadata or {})
    conference = dict(metadata.get("conference") or {})
    if conference.get("type") == "zoom" and conference.get("id"):
        try:
            await zoom_delete(db, meeting, conference)
        except IntegrationError as exc:
            warnings.append(f"The Zoom meeting couldn't be deleted: {exc}")
    link = dict(metadata.get("calendar") or {})
    pulled = meeting.provider in calendars.KEYS
    calendar_invites = bool(link.get("invites")) or pulled
    if link.get("event_id") and (not pulled or link.get("organizer_self")):
        try:
            connection = await calendars.link_connection(db, meeting, link)
            await calendars.cancel_event(db, connection, link, notify=notify, reason=reason)
            metadata["calendar"] = {**link, "state": "synced", "error": None, "attempts": 0, "next_attempt_at": None}
        except IntegrationError as exc:
            metadata["calendar"] = calendars.mark_pending(link, str(exc))
            warnings.append(f"The calendar event wasn't cancelled yet ({exc}). AIVA will keep trying.")
            calendar_invites = False
    elif link.get("state") == "pending" and not link.get("event_id"):
        metadata["calendar"] = {**link, "state": "synced", "error": None, "next_attempt_at": None}
    meeting.meeting_metadata = metadata
    return {"warnings": warnings, "calendar_invites": calendar_invites}


async def before_delete(db: AsyncSession, meeting: Meeting) -> None:
    """A scheduled, upcoming meeting deleted from AIVA is removed from Zoom and the calendar too."""

    if meeting.status != "scheduled" or meeting.start_at <= utcnow():
        return
    try:
        await after_cancel(db, meeting, reason=None, notify=True)
    except Exception:  # noqa: BLE001 - deleting the meeting must always work
        logger.exception("Cleaning up integrations for meeting %s failed", meeting.id)


async def meeting_options(db: AsyncSession, principal: Any) -> dict[str, Any]:
    """Which online-meeting types this person can pick, and which calendar meetings go to."""

    org, me = principal.organization_id, principal.user_id
    options: list[dict[str, Any]] = []
    for key, label in CONFERENCES.items():
        item = INTEGRATIONS[key]
        reason: str | None = None
        account: str | None = None
        if not principal.has(item.feature):
            reason = f"{label} isn't included in your plan."
        elif not await core.is_enabled(db, org, key):
            reason = f"An admin hasn't turned {label} on."
        else:
            needed = CALENDAR_FOR_CONFERENCE.get(key, key)
            if needed in calendars.KEYS and not await core.is_enabled(db, org, needed):
                reason = f"{label} links need {calendars.LABEL[needed]}, which is off."
            else:
                connection = await core.connection_for(db, org, needed, me)
                if connection is None:
                    reason = f"Connect your {INTEGRATIONS[needed].name} account in Integrations."
                elif connection.status == "needs_reauth":
                    reason = f"Reconnect {INTEGRATIONS[needed].name} in Integrations."
                else:
                    account = connection.account_label
        options.append({"key": key, "label": label, "available": reason is None, "reason": reason, "account": account, "setup_url": f"/integrations/{CALENDAR_FOR_CONFERENCE.get(key, key)}"})
    target = await calendars.default_target(db, org, me)
    calendar = None
    if target is not None:
        key, connection, settings = target
        calendar = {"key": key, "label": calendars.LABEL[key], "account": connection.account_label, "invites_from_calendar": bool(settings["invites_from_calendar"])}
    return {"options": options, "calendar": calendar}


# ---------------------------------------------------------------------------
# Microsoft Teams channel (bridged to the existing chat delivery)
# ---------------------------------------------------------------------------


async def _teams_row(db: AsyncSession, organization_id: uuid.UUID) -> Integration | None:
    return (await db.execute(select(Integration).where(Integration.organization_id == organization_id, Integration.kind == "teams"))).scalar_one_or_none()


def _check_teams_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise IntegrationError("Paste the https:// webhook URL from Teams.", 422)
    if not any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in TEAMS_HOSTS):
        raise IntegrationError(
            "That doesn't look like a Teams channel webhook. In Teams, use the channel's Workflows → "
            "\"Post to a channel when a webhook request is received\" and copy its URL.",
            422,
        )
    return url


def _invalidate(organization_id: uuid.UUID) -> None:
    from app.services.automation import invalidate_automation_cache

    invalidate_automation_cache(organization_id)


async def _teams_events(db: AsyncSession, organization_id: uuid.UUID) -> list[str]:
    from app.services.automation import CHAT_EVENTS

    row = await _teams_row(db, organization_id)
    if row is not None and row.events is not None:
        return [e for e in row.events if e in CHAT_EVENTS]
    config = await core.workspace_config(db, organization_id, "microsoft_teams")
    return [e for e in config.get("events") or TEAMS_DEFAULT_EVENTS if e in CHAT_EVENTS]


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    if key != "microsoft_teams":
        return {}
    url = _check_teams_url(values.get("webhook_url") or "")
    values["webhook_url"] = url
    return {"label": "Teams channel", "config": {"webhook_host": urlparse(url).hostname}}


async def _bridge_teams(db: AsyncSession, connection: IntegrationConnection, enabled: bool) -> None:
    url = core.secret(connection, "webhook_url")
    if not url:
        return
    row = await _teams_row(db, connection.organization_id)
    events = await _teams_events(db, connection.organization_id)
    if row is None:
        row = Integration(id=uuid.uuid4(), organization_id=connection.organization_id, kind="teams", config={}, events=events, connected_by_user_id=connection.connected_by_user_id)
        db.add(row)
    row.config = {**(row.config or {}), "webhook_url": encrypt(url)}
    row.events = events
    row.is_active = enabled
    row.last_error = None
    _invalidate(connection.organization_id)


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if key == "microsoft_teams":
        if connection.user_id is not None:
            return  # a personal Microsoft sign-in, not the channel webhook
        await _bridge_teams(db, connection, await core.is_enabled(db, connection.organization_id, key))
    elif key == "zoom":
        connection.config = {**ZOOM_DEFAULTS, **(connection.config or {})}


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if key == "microsoft_teams" and connection.user_id is None:
        row = await _teams_row(db, connection.organization_id)
        if row is not None:
            await db.delete(row)
            _invalidate(connection.organization_id)


async def _adopt_teams(db: AsyncSession, organization_id: uuid.UUID) -> IntegrationConnection | None:
    """A Teams webhook saved before the hub existed becomes the hub's connection."""

    if await core.connection_for(db, organization_id, "microsoft_teams") is not None:
        return None
    row = await _teams_row(db, organization_id)
    url = decrypt((row.config or {}).get("webhook_url")) if row else None
    if not url:
        return None
    connection = IntegrationConnection(
        id=uuid.uuid4(), organization_id=organization_id, provider="microsoft_teams", status="connected", account_label="Teams channel",
        webhook_token=core.new_webhook_token(), connected_by_user_id=row.connected_by_user_id if row else None,
        config={"webhook_host": urlparse(url).hostname},
    )
    core.set_secret(connection, "webhook_url", url)
    db.add(connection)
    await db.flush()
    core.log(db, organization_id, "microsoft_teams", "connected", "Picked up the Teams channel connected in Other connections.", level="success", connection=connection)
    return connection


async def on_toggle(db: AsyncSession, key: str, organization_id: uuid.UUID, enabled: bool) -> None:
    if key != "microsoft_teams":
        return
    if enabled:
        await _adopt_teams(db, organization_id)
    row = await _teams_row(db, organization_id)
    if row is not None:
        row.is_active = enabled
        _invalidate(organization_id)


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    if key == "zoom":
        me = (await core.request(db, connection, "GET", f"{ZOOM}/users/me")).json()
        plan = {1: "Basic", 2: "Licensed", 3: "On-prem"}.get(me.get("type"), "")
        return f"Signed in to Zoom as {me.get('email') or connection.account_label}" + (f" ({plan} account)." if plan else ".")
    from app.services import chat

    row = await _teams_row(db, connection.organization_id)
    url = (decrypt((row.config or {}).get("webhook_url")) if row else None) or core.secret(connection, "webhook_url")
    if not url:
        raise IntegrationError("Paste the channel's webhook URL first.", 409)
    who = getattr(principal, "full_name", None) or "An admin"
    try:
        await chat.post("teams", url, "AIVA is connected", f"{who} connected AIVA CRM to this channel. Alerts will appear here.", "/")
    except Exception as exc:  # noqa: BLE001 - httpx errors and non-2xx answers
        if row is not None:
            row.last_error = str(exc)[:500]
        raise IntegrationError(f"Teams didn't accept the message: {exc}", 400) from exc
    if row is not None:
        row.last_used_at = utcnow()
        row.last_error = None
    return "Posted a test card to the Teams channel."


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    if key == "zoom":
        connection = await core.connection_for(db, org, "zoom", principal.user_id)
        upcoming = (
            await db.execute(
                select(Meeting.id, Meeting.title, Meeting.start_at, Meeting.meeting_url)
                .where(
                    Meeting.organization_id == org,
                    Meeting.organizer_user_id == principal.user_id,
                    Meeting.status == "scheduled",
                    Meeting.start_at >= utcnow(),
                    Meeting.meeting_metadata["conference"]["type"].astext == "zoom",
                )
                .order_by(Meeting.start_at)
                .limit(8)
            )
        ).all()
        return {
            "settings": zoom_settings(connection),
            "upcoming": [{"id": str(i), "title": t, "start_at": s, "join_url": u} for i, t, s, u in upcoming],
        }
    from app.services.automation import CHAT_EVENTS

    row = await _teams_row(db, org)
    recent = (
        await db.execute(
            select(OutboundMessage).where(OutboundMessage.organization_id == org, OutboundMessage.channel == "teams").order_by(OutboundMessage.created_at.desc()).limit(10)
        )
    ).scalars().all()
    week = utcnow() - timedelta(days=7)
    counts = dict(
        (
            await db.execute(
                select(OutboundMessage.status, func.count())
                .where(OutboundMessage.organization_id == org, OutboundMessage.channel == "teams", OutboundMessage.created_at >= week)
                .group_by(OutboundMessage.status)
            )
        ).all()
    )
    outlook = await core.connection_for(db, org, "outlook_calendar", principal.user_id)
    return {
        "events": await _teams_events(db, org),
        "event_options": [{"key": k, "label": v} for k, v in CHAT_EVENTS.items()],
        "channel": {"active": bool(row and row.is_active), "last_used_at": row.last_used_at if row else None, "last_error": row.last_error if row else None},
        "recent": [
            {"id": str(m.id), "event": m.event, "title": (m.payload or {}).get("title"), "text": (m.payload or {}).get("text"), "status": m.status, "error": m.last_error, "at": m.sent_at or m.created_at}
            for m in recent
        ],
        "week": {"sent": int(counts.get("sent", 0)), "failed": int(counts.get("failed", 0)), "pending": int(counts.get("pending", 0))},
        "meeting_links": {
            "outlook_calendar_enabled": await core.is_enabled(db, org, "outlook_calendar"),
            "outlook_calendar_connected": bool(outlook and outlook.status != "needs_reauth"),
            "account": outlook.account_label if outlook else None,
        },
    }


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if key == "zoom":
        if not personal:
            return {}
        return {name: bool(config[name]) for name in ZOOM_DEFAULTS if name in config}
    if personal:
        return {}
    from app.services.automation import CHAT_EVENTS

    cleaned: dict[str, Any] = {}
    if "events" in config:
        events = [e for e in (config.get("events") or []) if isinstance(e, str) and e in CHAT_EVENTS]
        cleaned["events"] = list(dict.fromkeys(events))
        row = await _teams_row(db, principal.organization_id)
        if row is not None:
            row.events = cleaned["events"]
            _invalidate(principal.organization_id)
    return cleaned


async def run_due(db: AsyncSession) -> int:
    """Keep the older Teams connection (Other connections) and the hub in step.

    * connected there before the hub knew about it → switched on and adopted here;
    * switched off here → nothing goes to the channel, even if re-saved there;
    * switched on here without a hub connection → the saved webhook is adopted.
    """

    hub_connection = IntegrationConnection.__table__.alias("hub_teams")
    rows = (
        await db.execute(
            select(Integration, IntegrationToggle)
            .outerjoin(IntegrationToggle, and_(IntegrationToggle.organization_id == Integration.organization_id, IntegrationToggle.provider == "microsoft_teams"))
            .outerjoin(
                hub_connection,
                and_(hub_connection.c.organization_id == Integration.organization_id, hub_connection.c.provider == "microsoft_teams", hub_connection.c.user_id.is_(None)),
            )
            .where(
                Integration.kind == "teams",
                or_(
                    and_(IntegrationToggle.id.is_(None), Integration.is_active.is_(True)),
                    and_(IntegrationToggle.enabled.is_(False), Integration.is_active.is_(True)),
                    and_(IntegrationToggle.enabled.is_(True), hub_connection.c.id.is_(None)),
                ),
            )
            .limit(50)
        )
    ).all()
    handled = 0
    for row, toggle in rows:
        has_url = bool((row.config or {}).get("webhook_url"))
        if toggle is None:
            if not has_url:
                continue
            db.add(IntegrationToggle(id=uuid.uuid4(), organization_id=row.organization_id, provider="microsoft_teams", enabled=True, config={"events": list(row.events or [])}))
            await db.flush()
            await _adopt_teams(db, row.organization_id)
        elif not toggle.enabled:
            row.is_active = False
        elif has_url:
            await _adopt_teams(db, row.organization_id)
        else:
            continue
        _invalidate(row.organization_id)
        handled += 1
    if handled:
        await db.commit()
    return handled
