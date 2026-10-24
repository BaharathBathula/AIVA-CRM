"""Calendly: bookings become AIVA meetings, linked to the right contact or lead.

Connecting (an admin, once for the workspace) either signs in with Calendly
(OAuth) or uses a personal access token. AIVA then subscribes to
``invitee.created`` and ``invitee.canceled`` for the organization (or just the
connected user when they aren't a Calendly admin).

Webhooks are verified with ``Calendly-Webhook-Signature`` (``t=…,v1=…``, an
HMAC-SHA256 of ``"{t}.{raw body}"``). With a personal access token AIVA sets
its own signing key per subscription; OAuth apps have one key per app (the
server's ``CALENDLY_WEBHOOK_SIGNING_KEY``, or the key a workspace saves for its
own app). When no key is available, AIVA doesn't trust the message at all and
reads the booking back from the Calendly API instead.

Every booking is applied once (the invitee URI and event are remembered on the
meeting), so retried deliveries don't duplicate anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.integrations import core
from app.integrations.core import IntegrationError
from app.models.contact import Contact
from app.models.integration_hub import IntegrationConnection
from app.models.lead import Lead
from app.models.meeting import Meeting
from app.models.membership import OrganizationMembership
from app.models.user import User

KEY = "calendly"
API = "https://api.calendly.com"
EVENTS = ["invitee.created", "invitee.canceled"]
TOLERANCE_SECONDS = 300
HEALTH_EVERY = timedelta(hours=6)
DEFAULTS = {"create_leads": "new", "lead_owner": "host"}
ONLINE_TYPES = {
    "zoom": "Zoom",
    "zoom_conference": "Zoom",
    "google_conference": "Google Meet",
    "microsoft_teams_conference": "Microsoft Teams",
    "gotomeeting": "GoToMeeting",
    "gotomeeting_conference": "GoToMeeting",
    "webex_conference": "Webex",
}
LOCATION_LABELS = {
    "physical": "In person",
    "outbound_call": "Phone call (you call)",
    "inbound_call": "Phone call (they call)",
    "ask_invitee": "Location from the invitee",
    "custom": "Custom location",
    **ONLINE_TYPES,
}


def settings_for(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    for name in DEFAULTS:
        if config and config.get(name):
            merged[name] = config[name]
    return merged


def token_mode(connection: IntegrationConnection) -> bool:
    return (connection.config or {}).get("auth") == "token"


def _api_uri(value: Any, kind: str) -> str | None:
    """Only follow Calendly API addresses of the expected kind (no open redirects / SSRF)."""

    text = str(value or "")
    if re.fullmatch(rf"{re.escape(API)}/{kind}/[A-Za-z0-9_-]+(/invitees/[A-Za-z0-9_-]+)?", text):
        return text
    return None


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


async def _raw_get(url: str, token: str) -> dict[str, Any]:
    """A GET with a token that isn't stored yet (checking a pasted personal access token)."""

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise IntegrationError(f"Couldn't reach Calendly ({exc.__class__.__name__}). Try again.", 502) from exc
    if response.status_code == 401:
        raise IntegrationError("Calendly didn't accept that token. Create a new personal access token and paste it again.", 400)
    if response.status_code >= 400:
        raise IntegrationError(f"Calendly said: {response.text[:200]}", 400)
    return response.json()


async def validate_credentials(db: AsyncSession, key: str, organization_id: uuid.UUID, values: dict[str, str]) -> dict[str, Any]:
    token = (values.get("personal_access_token") or "").strip()
    if not token:
        raise IntegrationError("Paste a personal access token, or click Connect to sign in with Calendly instead.", 422)
    info = (await _raw_get(f"{API}/users/me", token)).get("resource") or {}
    # Tells on_connected (same request) that this connection uses the pasted token.
    db.sync_session.info["calendly_token_connect"] = True
    return {
        "label": info.get("email") or info.get("name") or "Calendly",
        "external_id": info.get("uri"),
        "config": {
            "auth": "token",
            "user_uri": info.get("uri"),
            "organization_uri": info.get("current_organization"),
            "scheduling_url": info.get("scheduling_url"),
            "name": info.get("name"),
        },
    }


async def on_connected(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    if db.sync_session.info.pop("calendly_token_connect", False):
        token = core.secret(connection, "personal_access_token")
        connection.access_token = encrypt(token) if token else connection.access_token
        connection.refresh_token = None
        connection.token_expires_at = None
        connection.config = {**(connection.config or {}), "auth": "token"}
    else:
        core.set_secret(connection, "personal_access_token", None)
        connection.config = {**(connection.config or {}), "auth": "oauth"}
    if not connection.webhook_token:
        connection.webhook_token = core.new_webhook_token()
    await subscribe(db, connection)


def _callback(connection: IntegrationConnection) -> str:
    url = core.webhook_url(KEY, connection.webhook_token) or ""
    if not url.startswith("https://"):
        raise IntegrationError(
            "Calendly only sends bookings to a public https:// address. Set APP_BASE_URL to AIVA's public address, then use “Subscribe again”.",
            409,
        )
    return url


async def _delete_subscription(db: AsyncSession, connection: IntegrationConnection, uri: str | None) -> None:
    uri = _api_uri(uri, "webhook_subscriptions")
    if not uri:
        return
    try:
        await core.request(db, connection, "DELETE", uri)
    except IntegrationError:
        pass


async def subscribe(db: AsyncSession, connection: IntegrationConnection) -> dict[str, Any]:
    """(Re)creates the webhook subscription for this connection."""

    url = _callback(connection)
    config = dict(connection.config or {})
    organization = config.get("organization_uri")
    user = config.get("user_uri")
    if not organization or not user:
        raise IntegrationError("Calendly didn't say which organization this account belongs to. Reconnect Calendly.", 409)
    await _delete_subscription(db, connection, config.get("webhook_subscription_uri"))
    # Clear anything left over for this address (a failed disconnect, a second connect).
    for params in ({"organization": organization, "scope": "organization"}, {"organization": organization, "user": user, "scope": "user"}):
        try:
            found = (await core.request(db, connection, "GET", f"{API}/webhook_subscriptions", params={**params, "count": 100})).json()
        except IntegrationError:
            continue
        for item in found.get("collection") or []:
            if item.get("callback_url") == url:
                await _delete_subscription(db, connection, item.get("uri"))

    signing_key = secrets.token_urlsafe(32) if token_mode(connection) else None
    body: dict[str, Any] = {"url": url, "events": EVENTS, "organization": organization, "scope": "organization"}
    if signing_key:
        body["signing_key"] = signing_key
    try:
        response = await core.request(db, connection, "POST", f"{API}/webhook_subscriptions", json=body)
    except IntegrationError:
        # Only Calendly organization admins can subscribe for everyone; fall back to this user's bookings.
        body.update({"scope": "user", "user": user})
        response = await core.request(db, connection, "POST", f"{API}/webhook_subscriptions", json=body)
    resource = response.json().get("resource") or {}
    core.set_secret(connection, "webhook_signing_key", signing_key)
    connection.config = {
        **config,
        "webhook_subscription_uri": resource.get("uri"),
        "webhook_scope": resource.get("scope") or body["scope"],
        "webhook_state": resource.get("state") or "active",
        "webhook_checked_at": core.utcnow().isoformat(),
    }
    connection.last_error = None
    scope = "the whole Calendly organization" if (resource.get("scope") or body["scope"]) == "organization" else "the connected user's bookings"
    core.log(db, connection.organization_id, KEY, "subscribed", f"Listening for new and cancelled bookings for {scope}.", level="success", connection=connection)
    return resource


async def on_disconnect(db: AsyncSession, key: str, connection: IntegrationConnection) -> None:
    await _delete_subscription(db, connection, (connection.config or {}).get("webhook_subscription_uri"))


async def test(db: AsyncSession, key: str, connection: IntegrationConnection, principal: Any) -> str:
    info = (await core.request(db, connection, "GET", f"{API}/users/me")).json().get("resource") or {}
    state = (connection.config or {}).get("webhook_state") or "not subscribed"
    return f"Calendly answered for {info.get('email') or info.get('name') or connection.account_label}; booking updates are {state}."


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


async def signing_key(db: AsyncSession, connection: IntegrationConnection) -> str | None:
    if token_mode(connection):
        return core.secret(connection, "webhook_signing_key")
    app = await core.oauth_app(db, connection.organization_id, "calendly")
    if app["source"] == "workspace":
        toggle = await core.toggle_row(db, connection.organization_id, KEY)
        return core.secret(toggle, "app_webhook_signing_key") if toggle else None
    return get_settings().calendly_webhook_signing_key or None


def verify_signature(header: str | None, body: bytes, key: str, now: float | None = None) -> bool:
    if not header:
        return False
    parts: dict[str, str] = {}
    for piece in header.split(","):
        name, _, value = piece.strip().partition("=")
        if name and value:
            parts[name] = value
    stamp, signature = parts.get("t", ""), parts.get("v1", "")
    if not (stamp.isascii() and stamp.isdigit()) or not signature:
        return False
    if abs((now or time.time()) - int(stamp)) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(key.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


# ---------------------------------------------------------------------------
# Bookings
# ---------------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _answers(invitee: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"question": str(q.get("question") or "")[:300], "answer": str(q.get("answer") or "")[:2000]}
        for q in invitee.get("questions_and_answers") or []
        if q.get("question")
    ]


def _answer(answers: list[dict[str, str]], pattern: str) -> str | None:
    for item in answers:
        if re.search(pattern, item["question"], re.I) and item["answer"].strip():
            return item["answer"].strip()
    return None


async def _members_by_email(db: AsyncSession, organization_id: uuid.UUID, emails: list[str]) -> dict[str, User]:
    emails = [e.lower() for e in emails if e]
    if not emails:
        return {}
    rows = (
        await db.execute(
            select(User)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == "active",
                func.lower(User.email).in_(emails),
            )
        )
    ).scalars().all()
    return {u.email.lower(): u for u in rows}


async def _is_member(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        await db.execute(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.status == "active",
            )
        )
    ).first() is not None


async def _meeting_for(db: AsyncSession, organization_id: uuid.UUID, event_uri: str) -> Meeting | None:
    return (
        await db.execute(
            select(Meeting)
            .where(Meeting.organization_id == organization_id, Meeting.provider == KEY, Meeting.external_event_id == event_uri)
            .with_for_update()
            .execution_options(skip_visibility=True)
        )
    ).scalar_one_or_none()


async def apply_booking(db: AsyncSession, connection: IntegrationConnection, event: str, invitee: dict[str, Any]) -> dict[str, Any]:
    """Applies one invitee.created / invitee.canceled. Safe to call twice for the same event."""

    org = connection.organization_id
    scheduled = invitee.get("scheduled_event") or {}
    event_uri = _api_uri(scheduled.get("uri") or invitee.get("event"), "scheduled_events")
    invitee_uri = str(invitee.get("uri") or "")[:500]
    if not event_uri or not invitee_uri:
        return {"ignored": "incomplete booking"}
    marker = f"{event}:{invitee_uri}"
    meeting = await _meeting_for(db, org, event_uri)
    if meeting is not None and marker in (meeting.meeting_metadata or {}).get("calendly_processed", []):
        return {"duplicate": True, "meeting_id": str(meeting.id)}
    if event == "invitee.created":
        return await _booked(db, connection, invitee, scheduled, event_uri, invitee_uri, marker, meeting)
    return await _cancelled(db, connection, invitee, scheduled, invitee_uri, marker, meeting)


def _remember(metadata: dict[str, Any], marker: str) -> list[str]:
    return ([m for m in metadata.get("calendly_processed", []) if m != marker] + [marker])[-200:]


async def _booked(
    db: AsyncSession,
    connection: IntegrationConnection,
    invitee: dict[str, Any],
    scheduled: dict[str, Any],
    event_uri: str,
    invitee_uri: str,
    marker: str,
    meeting: Meeting | None,
) -> dict[str, Any]:
    org = connection.organization_id
    email = str(invitee.get("email") or "").strip().lower()[:320]
    name = (invitee.get("name") or " ".join(x for x in [invitee.get("first_name"), invitee.get("last_name")] if x) or email or "Invitee").strip()[:200]
    start = _parse_time(scheduled.get("start_time"))
    end = _parse_time(scheduled.get("end_time"))
    if start is None or end is None or end <= start:
        return {"ignored": "booking without a valid time"}
    event_name = str(scheduled.get("name") or "Calendly meeting")[:300]
    location = scheduled.get("location") or {}
    kind = str(location.get("type") or "")
    from app.integrations.providers.calendars import safe_url

    join_url = safe_url(location.get("join_url")) or safe_url(location.get("location"))
    place = None if join_url else (location.get("location") or LOCATION_LABELS.get(kind) or None)
    answers = _answers(invitee)
    hosts = scheduled.get("event_memberships") or []
    members = await _members_by_email(db, org, [h.get("user_email") for h in hosts] + [g.get("email") for g in scheduled.get("event_guests") or []])
    organizer = next((members[h["user_email"].lower()] for h in hosts if h.get("user_email") and h["user_email"].lower() in members), None)

    contact = None
    lead = None
    created_lead = False
    if email:
        contact = (
            await db.execute(
                select(Contact)
                .where(Contact.organization_id == org, func.lower(Contact.email) == email, Contact.is_archived.is_(False))
                .order_by(Contact.created_at)
                .limit(1)
                .execution_options(skip_visibility=True)
            )
        ).scalar_one_or_none()
        if contact is None:
            lead = (
                await db.execute(
                    select(Lead)
                    .where(Lead.organization_id == org, func.lower(Lead.email) == email, Lead.converted_at.is_(None))
                    .order_by(Lead.created_at.desc())
                    .limit(1)
                    .execution_options(skip_visibility=True)
                )
            ).scalar_one_or_none()
        options = settings_for(await core.workspace_config(db, org, KEY))
        if contact is None and lead is None and options["create_leads"] != "never":
            owner_id: uuid.UUID | None = None
            if options["lead_owner"] == "host":
                owner_id = organizer.id if organizer else None
            elif options["lead_owner"] not in {"none", ""}:
                try:
                    candidate = uuid.UUID(str(options["lead_owner"]))
                    owner_id = candidate if await _is_member(db, org, candidate) else None
                except ValueError:
                    owner_id = None
            first = (invitee.get("first_name") or name.split(" ")[0] or email.split("@")[0]).strip()[:120]
            last = (invitee.get("last_name") or (" ".join(name.split(" ")[1:]) if " " in name else "")).strip()[:120]
            phone = invitee.get("text_reminder_number") or _answer(answers, r"phone|mobile|cell")
            lead = Lead(
                id=uuid.uuid4(),
                organization_id=org,
                first_name=first or "Calendly",
                last_name=last,
                email=email,
                phone=(phone or None) and str(phone)[:50],
                company_name=(_answer(answers, r"company|organi[sz]ation|business") or "")[:250] or None,
                source="Calendly",
                status="new",
                owner_user_id=owner_id,
                notes=f"Booked “{event_name}” on Calendly.",
            )
            db.add(lead)
            await db.flush()
            created_lead = True

    if contact is not None:
        attendee = {"type": "contact", "id": str(contact.id), "email": email, "name": name, "response": "accepted"}
    elif lead is not None:
        attendee = {"type": "lead", "id": str(lead.id), "email": email, "name": name, "response": "accepted"}
    else:
        attendee = {"type": "email", "id": None, "email": email or None, "name": name, "response": "accepted"}

    description_lines = [f"Booked on Calendly: {event_name}."]
    for item in answers:
        description_lines.append(f"{item['question']}: {item['answer']}")
    invitee_info = {
        "email": email,
        "name": name,
        "status": "active",
        "timezone": invitee.get("timezone"),
        "cancel_url": invitee.get("cancel_url"),
        "reschedule_url": invitee.get("reschedule_url"),
        "answers": answers,
        "rescheduled_from": invitee.get("old_invitee"),
    }

    if meeting is None:
        meeting = Meeting(
            id=uuid.uuid4(),
            organization_id=org,
            provider=KEY,
            external_event_id=event_uri[:500],
            title=(f"{event_name} with {name}" if name else event_name)[:500],
            description="\n".join(description_lines)[:10000],
            location=(place or None) and str(place)[:500],
            meeting_url=(join_url or None) and str(join_url)[:1000],
            start_at=start,
            end_at=end,
            timezone=(invitee.get("timezone") or None) and str(invitee.get("timezone"))[:100],
            status="scheduled",
            attendees=[],
            is_all_day=False,
            is_online=bool(join_url) or kind in ONLINE_TYPES,
            organizer_user_id=organizer.id if organizer else None,
            contact_id=contact.id if contact else None,
            lead_id=lead.id if lead and not contact else None,
            account_id=contact.account_id if contact else None,
            action_items=[],
            meeting_metadata={"source": "calendly", "event_type": scheduled.get("event_type"), "event_name": event_name, "location_type": kind or None},
        )
        try:
            async with db.begin_nested():
                db.add(meeting)
                await db.flush()
        except IntegrityError:
            # Another invitee of the same group event arrived at the same moment.
            meeting = await _meeting_for(db, org, event_uri)
            if meeting is None:
                raise
    else:
        meeting.start_at, meeting.end_at = start, end
        if scheduled.get("status") != "canceled":
            meeting.status = "scheduled"
        if meeting.contact_id is None and meeting.lead_id is None:
            meeting.contact_id = contact.id if contact else None
            meeting.lead_id = lead.id if lead and not contact else None
            meeting.account_id = meeting.account_id or (contact.account_id if contact else None)

    attendees = [dict(a) for a in meeting.attendees or []]
    keys = {(a.get("email") or "").lower() for a in attendees if a.get("email")}
    for host in hosts:
        host_email = (host.get("user_email") or "").lower()
        if not host_email or host_email in keys:
            continue
        member = members.get(host_email)
        if member:
            attendees.append({"type": "user", "id": str(member.id), "email": member.email, "name": member.full_name, "response": "accepted"})
        else:
            attendees.append({"type": "email", "id": None, "email": host_email, "name": host.get("user_name") or host_email, "response": "accepted"})
        keys.add(host_email)
    if attendee["email"] and attendee["email"] in keys:
        attendees = [attendee if (a.get("email") or "").lower() == attendee["email"] else a for a in attendees]
    else:
        attendees.append(attendee)
    for guest in scheduled.get("event_guests") or []:
        guest_email = (guest.get("email") or "").lower()
        if guest_email and guest_email not in keys and guest_email != attendee["email"]:
            attendees.append({"type": "email", "id": None, "email": guest_email, "name": guest_email, "response": "needs_action"})
            keys.add(guest_email)
    meeting.attendees = attendees
    metadata = dict(meeting.meeting_metadata or {})
    invitees = dict(metadata.get("invitees") or {})
    invitees[invitee_uri] = invitee_info
    metadata["invitees"] = invitees
    metadata["calendly_processed"] = _remember(metadata, marker)
    meeting.meeting_metadata = metadata

    if email:
        from app.services import outbound

        await outbound.meeting_booked(db, org, {email})
    when = start.strftime("%d %b %Y %H:%M UTC")
    who = f"{name} (new lead)" if created_lead else name
    core.log(
        db, org, KEY, "booking", f"{who} booked “{event_name}” for {when}.", level="success", connection=connection,
        detail={"meeting_id": str(meeting.id), "lead_id": str(lead.id) if lead else None, "contact_id": str(contact.id) if contact else None, "invitee": invitee_uri},
    )
    return {"meeting_id": str(meeting.id), "lead_created": created_lead, "lead_id": str(lead.id) if lead else None, "contact_id": str(contact.id) if contact else None}


async def _cancelled(
    db: AsyncSession,
    connection: IntegrationConnection,
    invitee: dict[str, Any],
    scheduled: dict[str, Any],
    invitee_uri: str,
    marker: str,
    meeting: Meeting | None,
) -> dict[str, Any]:
    org = connection.organization_id
    email = str(invitee.get("email") or "").strip().lower()
    name = invitee.get("name") or email or "The invitee"
    if meeting is None:
        core.log(db, org, KEY, "cancel_unknown", f"{name} cancelled a Calendly booking AIVA hadn't seen.", connection=connection)
        return {"ignored": "unknown booking"}
    metadata = dict(meeting.meeting_metadata or {})
    invitees = dict(metadata.get("invitees") or {})
    entry = dict(invitees.get(invitee_uri) or {"email": email, "name": name})
    entry["status"] = "canceled"
    invitees[invitee_uri] = entry
    metadata["invitees"] = invitees
    cancellation = invitee.get("cancellation") or {}
    rescheduled = bool(invitee.get("rescheduled"))
    meeting.attendees = [
        {**a, "response": "declined"} if email and (a.get("email") or "").lower() == email and a.get("type") != "user" else a for a in meeting.attendees or []
    ]
    still_coming = any(i.get("status") == "active" for i in invitees.values())
    everyone_gone = scheduled.get("status") == "canceled" or not still_coming
    if everyone_gone and meeting.status == "scheduled":
        meeting.status = "cancelled"
        metadata["cancel_reason"] = "Rescheduled on Calendly" if rescheduled else (cancellation.get("reason") or "Cancelled on Calendly")
        metadata["canceled_by"] = cancellation.get("canceled_by")
        metadata["sequence"] = int(metadata.get("sequence", 0)) + 1
        if rescheduled and invitee.get("new_invitee"):
            metadata["rescheduled_to"] = invitee.get("new_invitee")
    metadata["calendly_processed"] = _remember(metadata, marker)
    meeting.meeting_metadata = metadata
    verb = "rescheduled" if rescheduled else "cancelled"
    reason = f" ({cancellation['reason']})" if cancellation.get("reason") and not rescheduled else ""
    core.log(
        db, org, KEY, "booking_cancelled", f"{name} {verb} “{metadata.get('event_name') or meeting.title}”{reason}.", level="warning",
        connection=connection, detail={"meeting_id": str(meeting.id), "invitee": invitee_uri},
    )
    return {"meeting_id": str(meeting.id), "cancelled": meeting.status == "cancelled"}


async def handle_webhook(db: AsyncSession, key: str, connection: IntegrationConnection | None, request: Request) -> Response:
    if connection is None:
        return JSONResponse({"error": "unknown webhook"}, status_code=404)
    if request.method == "GET":
        return JSONResponse({"ok": True})
    body = await request.body()
    if len(body) > 1024 * 1024:
        return JSONResponse({"error": "too large"}, status_code=413)
    org = connection.organization_id
    key_value = await signing_key(db, connection)
    verified = False
    if key_value:
        if not verify_signature(request.headers.get("Calendly-Webhook-Signature"), body, key_value):
            core.log(db, org, KEY, "webhook_rejected", "A Calendly message with a missing, wrong or stale signature was refused.", level="warning", connection=connection)
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        verified = True
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    event = data.get("event") if isinstance(data, dict) else None
    payload = data.get("payload") if isinstance(data, dict) and isinstance(data.get("payload"), dict) else {}
    if event not in EVENTS:
        return JSONResponse({"ignored": f"event {event!s} isn't used"})
    try:
        if not verified:
            # Without a signing key, nothing in the message is trusted: read the booking from Calendly.
            invitee_uri = _api_uri(payload.get("uri"), "scheduled_events")
            if not invitee_uri or "/invitees/" not in invitee_uri:
                return JSONResponse({"error": "not a Calendly invitee"}, status_code=400)
            invitee = (await core.request(db, connection, "GET", invitee_uri)).json().get("resource") or {}
            event_uri = _api_uri(invitee.get("event"), "scheduled_events")
            if not event_uri:
                return JSONResponse({"error": "booking not found"}, status_code=400)
            invitee["scheduled_event"] = (await core.request(db, connection, "GET", event_uri)).json().get("resource") or {}
            payload = invitee
            event = "invitee.canceled" if invitee.get("status") == "canceled" else "invitee.created"
        result = await apply_booking(db, connection, event, payload)
    except IntegrationError as exc:
        core.log(db, org, KEY, "webhook_failed", f"Couldn't process a Calendly booking: {exc}", level="error", connection=connection)
        return JSONResponse({"error": "retry later"}, status_code=503 if exc.status_code >= 500 else 200)
    return JSONResponse({"ok": True, **result})


# ---------------------------------------------------------------------------
# Scheduling links
# ---------------------------------------------------------------------------


def _event_type_out(item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("profile") or {}
    return {
        "uri": item.get("uri"),
        "name": item.get("name"),
        "scheduling_url": item.get("scheduling_url"),
        "duration": item.get("duration"),
        "kind": item.get("kind"),
        "pooling_type": item.get("pooling_type"),
        "color": item.get("color"),
        "description": (item.get("description_plain") or "")[:300] or None,
        "owner": profile.get("name"),
        "secret": bool(item.get("secret")),
    }


async def links_for(db: AsyncSession, connection: IntegrationConnection, principal: Any) -> dict[str, Any]:
    """The signed-in person's Calendly links (matched by email), else the organization's."""

    config = connection.config or {}
    organization = config.get("organization_uri")
    me: dict[str, Any] | None = None
    if organization and principal.email:
        try:
            found = (
                await core.request(db, connection, "GET", f"{API}/organization_memberships", params={"organization": organization, "email": principal.email.lower()})
            ).json()
            first = (found.get("collection") or [None])[0]
            me = (first or {}).get("user") or None
        except IntegrationError:
            me = None
    params: dict[str, Any] = {"active": "true", "count": 100, "sort": "name:asc"}
    scope = "mine"
    if me and me.get("uri"):
        params["user"] = me["uri"]
    else:
        scope = "organization"
        params["organization"] = organization
    try:
        data = (await core.request(db, connection, "GET", f"{API}/event_types", params=params)).json()
    except IntegrationError:
        scope = "connected_user"
        data = (await core.request(db, connection, "GET", f"{API}/event_types", params={"active": "true", "count": 100, "user": config.get("user_uri")})).json()
    items = [_event_type_out(i) for i in data.get("collection") or [] if not i.get("secret")]
    return {
        "scope": scope,
        "scheduling_url": (me or {}).get("scheduling_url") or (config.get("scheduling_url") if scope == "connected_user" else None),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Page details, settings, health checks
# ---------------------------------------------------------------------------


async def detail(db: AsyncSession, key: str, principal: Any) -> dict[str, Any]:
    org = principal.organization_id
    connection = await core.connection_for(db, org, KEY)
    toggle = await core.toggle_row(db, org, KEY)
    app = await core.oauth_app(db, org, "calendly")
    since = core.utcnow() - timedelta(days=30)
    booked = (
        await db.execute(
            select(func.count()).select_from(Meeting).where(Meeting.organization_id == org, Meeting.provider == KEY, Meeting.created_at >= since)
        )
    ).scalar_one()
    upcoming = (
        await db.execute(
            select(func.count()).select_from(Meeting).where(
                Meeting.organization_id == org, Meeting.provider == KEY, Meeting.status == "scheduled", Meeting.start_at >= core.utcnow()
            )
        )
    ).scalar_one()
    recent_rows = (
        await db.execute(select(Meeting).where(Meeting.organization_id == org, Meeting.provider == KEY).order_by(Meeting.created_at.desc()).limit(8))
    ).scalars().all()
    recent = [
        {
            "id": str(m.id),
            "title": m.title,
            "start_at": m.start_at,
            "status": m.status,
            "href": f"/meetings?open={m.id}",
            "lead_id": str(m.lead_id) if m.lead_id else None,
            "contact_id": str(m.contact_id) if m.contact_id else None,
        }
        for m in recent_rows
    ]
    out: dict[str, Any] = {
        "settings": settings_for(toggle.config if toggle else {}),
        "bookings": {"last_30_days": int(booked), "upcoming": int(upcoming), "recent": recent},
        "signing": {
            "app_source": app["source"],
            "workspace_key_set": bool(toggle and core.secret(toggle, "app_webhook_signing_key")),
            "server_key_set": bool(get_settings().calendly_webhook_signing_key),
        },
        "webhook": None,
        "links": None,
    }
    if connection is None:
        return out
    config = connection.config or {}
    key_value = await signing_key(db, connection)
    out["auth"] = "token" if token_mode(connection) else "oauth"
    out["webhook"] = {
        "url": core.webhook_url(KEY, connection.webhook_token),
        "subscribed": bool(config.get("webhook_subscription_uri")),
        "scope": config.get("webhook_scope"),
        "state": config.get("webhook_state"),
        "checked_at": config.get("webhook_checked_at"),
        "verified": bool(key_value),
    }
    try:
        out["links"] = await links_for(db, connection, principal)
    except IntegrationError as exc:
        out["links_error"] = str(exc)
    return out


async def clean_settings(db: AsyncSession, key: str, principal: Any, config: dict[str, Any], personal: bool) -> dict[str, Any]:
    if personal:
        raise IntegrationError("Calendly is set up for the whole workspace.", 422)
    cleaned: dict[str, Any] = {}
    for name, value in config.items():
        if name == "create_leads":
            if value not in {"new", "never"}:
                raise IntegrationError("Choose whether bookings from new people create leads.", 422)
            cleaned[name] = value
        elif name == "lead_owner":
            value = str(value or "")
            if value not in {"host", "none"}:
                try:
                    candidate = uuid.UUID(value)
                except ValueError as exc:
                    raise IntegrationError("Choose who owns leads from bookings.", 422) from exc
                if not await _is_member(db, principal.organization_id, candidate):
                    raise IntegrationError("That person isn't an active member of this workspace.", 422)
                value = str(candidate)
            cleaned[name] = value
        else:
            raise IntegrationError(f"Unknown setting “{name}”.", 422)
    return cleaned


async def run_due(db: AsyncSession) -> int:
    """Every few hours, make sure Calendly is still sending bookings (it disables failing webhooks)."""

    now = core.utcnow()
    rows = (
        await db.execute(select(IntegrationConnection).where(IntegrationConnection.provider == KEY, IntegrationConnection.status == "connected").limit(200))
    ).scalars().all()
    handled = 0
    for connection in rows:
        config = connection.config or {}
        checked = _parse_time(config.get("webhook_checked_at"))
        if checked is not None and now - checked < HEALTH_EVERY:
            continue
        if not await core.is_enabled(db, connection.organization_id, KEY):
            continue
        state = None
        try:
            uri = _api_uri(config.get("webhook_subscription_uri"), "webhook_subscriptions")
            if uri:
                state = ((await core.request(db, connection, "GET", uri)).json().get("resource") or {}).get("state")
            if state != "active":
                await subscribe(db, connection)
                core.log(
                    db, connection.organization_id, KEY, "resubscribed",
                    "Calendly had stopped sending bookings to AIVA; AIVA subscribed again.", level="warning", connection=connection,
                )
                handled += 1
            else:
                connection.config = {**config, "webhook_state": state, "webhook_checked_at": now.isoformat()}
        except IntegrationError as exc:
            connection.config = {**(connection.config or {}), "webhook_checked_at": now.isoformat()}
            if connection.last_error != str(exc):
                connection.last_error = str(exc)[:1000]
                core.log(db, connection.organization_id, KEY, "health_check_failed", f"Couldn't check the Calendly subscription: {exc}", level="warning", connection=connection)
        await db.commit()
    return handled


def scheduling_link_for(event_type: dict[str, Any], name: str | None = None, email: str | None = None) -> str | None:
    """A scheduling link with the invitee's name and email filled in."""

    url = event_type.get("scheduling_url")
    if not url:
        return None
    params = []
    if name:
        params.append(f"name={quote(name)}")
    if email:
        params.append(f"email={quote(email)}")
    return f"{url}{'&' if '?' in url else '?'}{'&'.join(params)}" if params else url
