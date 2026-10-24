"""Integration endpoints for mail, calendars and meetings (Gmail, Outlook Mail,
Google / Outlook Calendar, Google Meet, Zoom, Microsoft Teams).

Connecting, switching on/off, settings and tests go through the hub
(``/integration-hub/{key}/…``); these are the extra actions the pages and the
meeting form need. Provider logic lives in ``app/integrations/providers``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.catalog import INTEGRATIONS
from app.integrations.core import IntegrationError
from app.integrations.providers import calendars, mail
from app.integrations.providers import meetings as meeting_links
from app.models.editions import MailboxConnection
from app.models.meeting import Meeting
from app.services import mailer

router = APIRouter(prefix="/integration-hub/mail-calendar", tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

MAIL_KEYS = mail.KEYS
SYNC_KEYS = (*mail.KEYS, *calendars.KEYS)


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _check(principal: Principal, key: str, allowed: tuple[str, ...]) -> None:
    if key not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown integration.")
    if not principal.has(INTEGRATIONS[key].feature):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"{INTEGRATIONS[key].name} isn't included in your plan.")
    if principal.api_key_id is not None or principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Use your own sign-in for your mailbox and calendar.")


async def _api_mailbox(db: AsyncSession, principal: Principal, key: str) -> MailboxConnection:
    mailbox = (
        await db.execute(
            select(MailboxConnection).where(MailboxConnection.organization_id == principal.organization_id, MailboxConnection.user_id == principal.user_id)
        )
    ).scalar_one_or_none()
    if mailbox is None or mailbox.transport != mail.TRANSPORT[key]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Connect {INTEGRATIONS[key].name} first.")
    return mailbox


@router.get("/meeting-options")
async def meeting_options(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """What the meeting form can offer this person: Meet / Zoom / Teams links and the calendar meetings go to."""

    return await meeting_links.meeting_options(db, principal)


@router.post("/{key}/sync")
async def sync_now(key: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Read new inbox messages or calendar changes for your own connection right away."""

    _check(principal, key, SYNC_KEYS)
    org = principal.organization_id
    try:
        if key in MAIL_KEYS:
            mailbox = await _api_mailbox(db, principal, key)
            result = await mail.sync_mailbox_now(db, mailbox)
            noun = "email" if result["imported"] == 1 else "emails"
            message = f"Checked {result['checked']} new message{'s' if result['checked'] != 1 else ''}; added {result['imported']} customer {noun}."
        else:
            connection = await core.active_connection(db, org, key, principal.user_id)
            try:
                result = await calendars.sync_connection(db, connection)
            except IntegrationError:
                await db.commit()
                raise
            await db.commit()
            message = f"Calendar checked: {result['created']} new, {result['updated']} updated, {result['cancelled']} cancelled."
    except IntegrationError as exc:
        raise _error(exc) from exc
    return {"ok": True, "message": message, "result": result}


@router.post("/{key}/send-test")
async def send_test_email(key: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Send a test email from your connected mailbox to yourself."""

    _check(principal, key, MAIL_KEYS)
    mailbox = await _api_mailbox(db, principal, key)
    if not await mailer.api_mailbox_usable(db, mailbox):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{INTEGRATIONS[key].name} is off or needs to be reconnected.")
    try:
        await mailer.send(
            mailer.mailbox_config(mailbox),
            to=mailbox.email_address,
            to_name=mailbox.display_name,
            subject="AIVA is sending through your mailbox",
            text=(
                f"This test was sent by AIVA CRM through {INTEGRATIONS[key].name}.\n\n"
                "Emails, campaigns and sequence steps you send from AIVA now come from this address, "
                "and customer replies in your inbox are added to their records."
            ),
        )
    except mailer.MailError as exc:
        core.log(db, principal.organization_id, key, "test_email_failed", str(exc), level="error", user_id=principal.user_id)
        await db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    core.log(db, principal.organization_id, key, "test_email_sent", f"Sent a test email to {mailbox.email_address}.", level="success", user_id=principal.user_id)
    await db.commit()
    return {"ok": True, "message": f"Test email sent to {mailbox.email_address}."}


@router.post("/{key}/use-mailbox")
async def use_for_sending(key: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Make your connected Gmail / Outlook account your AIVA mailbox again (after using an app password or the other one)."""

    _check(principal, key, MAIL_KEYS)
    try:
        connection = await core.active_connection(db, principal.organization_id, key, principal.user_id)
        await mail.on_connected(db, key, connection)
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()
    return {"ok": True, "message": f"AIVA now sends as {connection.account_label} through {INTEGRATIONS[key].name}."}


@router.get("/{key}/calendars")
async def my_calendars(key: str, principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    """Calendars you can add meetings to (for choosing which one to sync)."""

    _check(principal, key, calendars.KEYS)
    try:
        connection = await core.active_connection(db, principal.organization_id, key, principal.user_id)
        items = await calendars.list_calendars(db, connection)
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()
    return {"items": items, "selected": calendars.settings_of(connection).get("calendar_id")}


@router.post("/meetings/{meeting_id}/retry")
async def retry_calendar_push(meeting_id: uuid.UUID, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Try again to put a meeting in its calendar after a failure."""

    meeting = await db.get(Meeting, meeting_id)
    if meeting is None or meeting.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found.")
    link = dict((meeting.meeting_metadata or {}).get("calendar") or {})
    if not link:
        raise HTTPException(status.HTTP_409_CONFLICT, "This meeting isn't linked to a calendar.")
    is_manager = principal.can("records.view_all") and principal.can("records.delete")
    if link.get("user_id") != str(principal.user_id) and meeting.organizer_user_id != principal.user_id and not is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the organizer or a manager can retry this.")
    meeting.meeting_metadata = {**(meeting.meeting_metadata or {}), "calendar": {**link, "attempts": 0, "state": "pending"}}
    ok = await calendars.retry_push(db, meeting)
    await db.commit()
    calendar = meeting_links.calendar_out(meeting)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, (calendar or {}).get("error") or "The calendar couldn't be updated.")
    return {"ok": True, "calendar_sync": calendar}
