"""Outbound email: sequences, sending protection, tracking and domain health.

* **Sequences** – enrollments advance step by step. Automatic emails go out
  from the enrolling person's connected mailbox (or the server's mail
  settings) inside the sending window; manual steps become tasks that move
  the enrollment on when they're completed.
* **Sending protection** – sending days and hours, holidays, per-mailbox
  daily and hourly caps, a minimum gap between emails, an automatic pause
  when a mailbox's bounce rate climbs, the do-not-contact list and role
  addresses.
* **Tracking** – an open pixel, rewritten links (signed, so they can't be
  used as an open redirect) and an unsubscribe link in every email.
* **Domains** – SPF, DKIM, DMARC, MX and tracking-domain checks through
  DNS-over-HTTPS.
"""

from __future__ import annotations

import email.utils
import hashlib
import hmac
import html
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import MailboxConnection
from app.models.governance import SuppressionEntry
from app.models.lead import Lead
from app.models.organization import Organization
from app.models.outbound import Sequence, SequenceEnrollment, SequenceMessage, SequenceStep
from app.models.platform import Notification
from app.models.task import Task
from app.models.user import User
from app.services import mailer, workspace_settings
from app.services.merge import record_context, render

logger = logging.getLogger("aiva.outbound")

STEP_KINDS = {
    "auto_email": "Automatic email",
    "manual_email": "Manual email",
    "call": "Phone call",
    "linkedin": "LinkedIn task",
    "task": "Task",
}
TASK_TYPES = {"manual_email": "email", "call": "call", "linkedin": "follow_up", "task": "follow_up"}
FINISHED = {"finished", "replied", "bounced", "unsubscribed", "meeting_booked", "removed", "failed"}
LINK_RE = re.compile(r"(https?://[^\s<>\"')\]]+)")
BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail delivery")
BOUNCE_SUBJECT = re.compile(r"(undeliver|delivery status notification|delivery failure|returned mail|failure notice|mail delivery failed|could not be delivered)", re.I)
EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SPAM_WORDS = ("free", "guarantee", "act now", "limited time", "click here", "100%", "risk-free", "winner", "urgent", "$$$", "no obligation", "cash", "buy now")
MODELS = {"contact": Contact, "lead": Lead}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


def _base_url(tracking: workspace_settings.TrackingSettings | None = None) -> str:
    if tracking and tracking.tracking_domain:
        return f"https://{tracking.tracking_domain}"
    return get_settings().app_base_url.rstrip("/")


def click_signature(token: str, url: str) -> str:
    key = get_settings().jwt_secret_key.encode()
    return hmac.new(key, f"{token}|{url}".encode(), hashlib.sha256).hexdigest()[:20]


def click_url(token: str, url: str, tracking: workspace_settings.TrackingSettings | None = None) -> str:
    return f"{_base_url(tracking)}/api/aiva/public/click/{token}?u={quote(url, safe='')}&s={click_signature(token, url)}"


def open_url(token: str, tracking: workspace_settings.TrackingSettings | None = None) -> str:
    return f"{_base_url(tracking)}/api/aiva/public/email/open/{token}.gif"


def unsubscribe_link(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/u/{token}"


def build_html(body: str, *, token: str, tracking: workspace_settings.TrackingSettings) -> str:
    escaped = html.escape(body or "")

    def link(match: re.Match[str]) -> str:
        url = html.unescape(match.group(1))
        href = click_url(token, url, tracking) if tracking.click_tracking else url
        return f'<a href="{html.escape(href)}">{html.escape(url)}</a>'

    linked = LINK_RE.sub(link, escaped)
    paragraphs = "".join(f"<p style=\"margin:0 0 14px\">{part.replace(chr(10), '<br>')}</p>" for part in linked.split("\n\n") if part.strip())
    parts = ['<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#18171c">', paragraphs]
    if tracking.unsubscribe_link:
        parts.append(f'<p style="margin:24px 0 0;font-size:12px;color:#7a7784"><a style="color:#7a7784" href="{unsubscribe_link(token)}">{html.escape(tracking.unsubscribe_text)}</a></p>')
    if tracking.open_tracking:
        parts.append(f'<img src="{open_url(token, tracking)}" width="1" height="1" alt="" style="display:block;border:0">')
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Sending window and limits
# ---------------------------------------------------------------------------


def in_window(protection: workspace_settings.SendingProtection, moment: datetime) -> bool:
    local = moment.astimezone(ZoneInfo(protection.timezone))
    if local.weekday() not in protection.send_days or local.date().isoformat() in protection.holidays:
        return False
    return protection.window_start_hour <= local.hour < protection.window_end_hour


def next_window(protection: workspace_settings.SendingProtection, moment: datetime) -> datetime:
    """The first moment at or after ``moment`` inside the sending window."""

    tz = ZoneInfo(protection.timezone)
    local = moment.astimezone(tz)
    for day in range(0, 15):
        candidate_day = (local + timedelta(days=day)).date()
        if candidate_day.weekday() not in protection.send_days or candidate_day.isoformat() in protection.holidays:
            continue
        start = datetime(candidate_day.year, candidate_day.month, candidate_day.day, protection.window_start_hour, tzinfo=tz)
        end = datetime(candidate_day.year, candidate_day.month, candidate_day.day, min(protection.window_end_hour, 23), 59 if protection.window_end_hour == 24 else 0, tzinfo=tz)
        if day == 0:
            if local < start:
                return start.astimezone(timezone.utc)
            if local < end:
                return moment
            continue
        return start.astimezone(timezone.utc)
    return moment + timedelta(days=1)


def mailbox_limit(protection: workspace_settings.SendingProtection, user_id: uuid.UUID | None) -> int:
    if user_id and str(user_id) in protection.mailbox_limits:
        return int(protection.mailbox_limits[str(user_id)])
    return protection.daily_limit_per_mailbox


async def mailbox_stats(db: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID | None, protection: workspace_settings.SendingProtection) -> dict[str, Any]:
    now = utcnow()
    tz = ZoneInfo(protection.timezone)
    local_midnight = datetime.combine(now.astimezone(tz).date(), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    base = select(func.count()).select_from(SequenceMessage).where(
        SequenceMessage.organization_id == organization_id,
        SequenceMessage.sender_user_id == user_id,
        SequenceMessage.status == "sent",
        SequenceMessage.kind.in_(("auto_email", "manual_email")),
    )
    today = (await db.execute(base.where(SequenceMessage.sent_at >= local_midnight))).scalar_one()
    hour = (await db.execute(base.where(SequenceMessage.sent_at >= now - timedelta(hours=1)))).scalar_one()
    last = (
        await db.execute(
            select(func.max(SequenceMessage.sent_at)).where(
                SequenceMessage.organization_id == organization_id, SequenceMessage.sender_user_id == user_id, SequenceMessage.status == "sent"
            )
        )
    ).scalar_one()
    recent = (
        await db.execute(
            select(SequenceMessage.bounced_at)
            .where(
                SequenceMessage.organization_id == organization_id,
                SequenceMessage.sender_user_id == user_id,
                SequenceMessage.status == "sent",
            )
            .order_by(SequenceMessage.sent_at.desc())
            .limit(protection.bounce_window)
        )
    ).scalars().all()
    bounced = sum(1 for b in recent if b is not None)
    rate = round(bounced / len(recent) * 100, 1) if recent else 0.0
    paused = len(recent) >= 20 and rate >= protection.bounce_pause_percent
    return {
        "sent_today": int(today),
        "sent_last_hour": int(hour),
        "daily_limit": mailbox_limit(protection, user_id),
        "hourly_limit": protection.hourly_limit_per_mailbox,
        "last_sent_at": last,
        "bounce_rate": rate,
        "sample": len(recent),
        "paused": paused,
    }


async def suppressed(db: AsyncSession, organization_id: uuid.UUID, email: str) -> SuppressionEntry | None:
    from app.services.prospecting import suppression_for

    return await suppression_for(db, organization_id, email)


# ---------------------------------------------------------------------------
# Sequences: enrolling
# ---------------------------------------------------------------------------


async def active_steps(db: AsyncSession, sequence_id: uuid.UUID) -> list[SequenceStep]:
    return list(
        (
            await db.execute(
                select(SequenceStep).where(SequenceStep.sequence_id == sequence_id, SequenceStep.is_active.is_(True)).order_by(SequenceStep.position)
            )
        ).scalars()
    )


def step_delay(step: SequenceStep) -> timedelta:
    return timedelta(days=step.delay_days or 0, hours=step.delay_hours or 0)


async def set_contact_stage(db: AsyncSession, enrollment: SequenceEnrollment, stage: str | None) -> None:
    if enrollment.record_type != "contact" or not stage:
        return
    contact = await db.get(Contact, enrollment.record_id)
    stages = await workspace_settings.get(db, enrollment.organization_id, "stages")
    if contact is not None and stage in workspace_settings.stage_keys(stages, "contact"):
        contact.stage = stage


async def enroll(
    db: AsyncSession,
    principal: Any,
    sequence: Sequence,
    record_type: str,
    record_ids: list[uuid.UUID],
    sender_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if sequence.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn the sequence on before enrolling people.")
    steps = await active_steps(db, sequence.id)
    if not steps:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one step first.")
    needs_email = any(s.kind in ("auto_email", "manual_email") for s in steps)
    defaults = await workspace_settings.get(db, principal.organization_id, "sequences")
    protection = await workspace_settings.get(db, principal.organization_id, "sending")
    prospecting = await workspace_settings.get(db, principal.organization_id, "prospecting")
    sender = sender_user_id or principal.user_id
    model = MODELS[record_type]
    records = (await db.execute(select(model).where(model.organization_id == principal.organization_id, model.id.in_(record_ids)))).scalars().all()
    enrolled, skipped = [], []
    now = utcnow()
    for record in records:
        name = f"{record.first_name or ''} {record.last_name or ''}".strip() or record.email or "Unnamed"
        email = (record.email or "").strip().lower() or None

        def skip(reason: str) -> None:
            skipped.append({"id": str(record.id), "name": name, "reason": reason})

        if needs_email and not email:
            skip("No email address")
            continue
        if email and getattr(record, "email_opt_out", False):
            skip("Unsubscribed")
            continue
        if email and await suppressed(db, principal.organization_id, email):
            skip("On the do-not-contact list")
            continue
        if email and protection.skip_role_addresses and needs_email:
            from app.services.prospecting import is_role_address

            if is_role_address(email):
                skip("Shared inbox (role address)")
                continue
        if record_type == "contact" and getattr(record, "stage", None) in {"do_not_contact", "bad_data"}:
            skip("Contact stage is do not contact")
            continue
        existing = (
            await db.execute(
                select(SequenceEnrollment).where(
                    SequenceEnrollment.sequence_id == sequence.id, SequenceEnrollment.record_type == record_type, SequenceEnrollment.record_id == record.id
                )
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status in {"active", "paused"}:
            skip("Already in this sequence")
            continue
        if existing is not None and not defaults.allow_reenroll:
            skip("Was in this sequence before")
            continue
        if defaults.one_active_sequence:
            other = (
                await db.execute(
                    select(Sequence.name)
                    .join(SequenceEnrollment, SequenceEnrollment.sequence_id == Sequence.id)
                    .where(
                        SequenceEnrollment.organization_id == principal.organization_id,
                        SequenceEnrollment.record_type == record_type,
                        SequenceEnrollment.record_id == record.id,
                        SequenceEnrollment.status.in_(("active", "paused")),
                        SequenceEnrollment.sequence_id != sequence.id,
                    )
                )
            ).scalars().first()
            if other:
                skip(f"Already in “{other}”")
                continue
        if existing is not None:
            await db.delete(existing)
            await db.flush()
        enrollment = SequenceEnrollment(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            sequence_id=sequence.id,
            record_type=record_type,
            record_id=record.id,
            email=email,
            name=name[:200],
            sender_user_id=sender,
            status="active",
            current_position=0,
            next_run_at=now + step_delay(steps[0]),
            enrolled_by_user_id=principal.user_id,
        )
        db.add(enrollment)
        await set_contact_stage(db, enrollment, prospecting.sequence_stage)
        enrolled.append({"id": str(record.id), "name": name})
    return {"enrolled": enrolled, "skipped": skipped}


async def finish(db: AsyncSession, enrollment: SequenceEnrollment, status_value: str, reason: str | None = None) -> None:
    enrollment.status = status_value
    enrollment.finished_at = utcnow()
    enrollment.finished_reason = reason or status_value
    enrollment.next_run_at = None
    prospecting = await workspace_settings.get(db, enrollment.organization_id, "prospecting")
    if status_value == "replied":
        await set_contact_stage(db, enrollment, prospecting.replied_stage)
    elif status_value == "finished":
        await set_contact_stage(db, enrollment, prospecting.finished_stage)
    elif status_value == "bounced":
        await set_contact_stage(db, enrollment, "bad_data")
    # Open tasks for this enrollment aren't needed any more.
    if status_value != "finished":
        for task in (
            await db.execute(
                select(Task).where(
                    Task.organization_id == enrollment.organization_id,
                    Task.status.in_(("open", "in_progress")),
                    Task.task_metadata["sequence_enrollment_id"].astext == str(enrollment.id),
                )
            )
        ).scalars():
            task.status = "cancelled"


# ---------------------------------------------------------------------------
# Sequences: running steps
# ---------------------------------------------------------------------------


async def _record(db: AsyncSession, enrollment: SequenceEnrollment) -> Any:
    model = MODELS.get(enrollment.record_type)
    return await db.get(model, enrollment.record_id) if model else None


async def _render(db: AsyncSession, record: Any, sender: User | None, organization: Organization | None, template: str | None, token: str) -> str:
    context = await record_context(db, record, sender=sender, organization=organization, token=token)
    return render(template, context)


async def send_email_step(
    db: AsyncSession,
    enrollment: SequenceEnrollment,
    step: SequenceStep,
    *,
    subject_override: str | None = None,
    body_override: str | None = None,
    enforce_limits: bool = True,
) -> SequenceMessage:
    org = enrollment.organization_id
    record = await _record(db, enrollment)
    if record is None:
        await finish(db, enrollment, "removed", "Record deleted")
        raise ValueError("The person was deleted.")
    tracking = await workspace_settings.get(db, org, "tracking")
    defaults = await workspace_settings.get(db, org, "sequences")
    sender = await db.get(User, enrollment.sender_user_id) if enrollment.sender_user_id else None
    organization = await db.get(Organization, org)
    token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    subject = subject_override if subject_override is not None else await _render(db, record, sender, organization, step.subject, token)
    body = body_override if body_override is not None else await _render(db, record, sender, organization, step.body, token)
    headers: dict[str, str] = {
        "List-Unsubscribe": f"<{unsubscribe_link(token)}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    if defaults.thread_follow_ups and step.thread_reply and enrollment.thread_message_id:
        subject = enrollment.thread_subject if (enrollment.thread_subject or "").lower().startswith("re:") else f"Re: {enrollment.thread_subject or subject}"
        headers["In-Reply-To"] = enrollment.thread_message_id
        headers["References"] = enrollment.thread_message_id
    subject = (subject or "(no subject)")[:300]
    message = SequenceMessage(
        id=uuid.uuid4(),
        organization_id=org,
        enrollment_id=enrollment.id,
        sequence_id=enrollment.sequence_id,
        step_id=step.id,
        kind=step.kind,
        to_email=enrollment.email,
        subject=subject,
        body=body,
        status="sent",
        token=token,
        sender_user_id=enrollment.sender_user_id,
    )
    config = await mailer.sender_config(db, org, enrollment.sender_user_id)
    text = body + (f"\n\n—\n{tracking.unsubscribe_text} {unsubscribe_link(token)}" if tracking.unsubscribe_link else "")
    sender_domain = (config.from_email.split("@")[-1] if config else None) or "aiva.local"
    message.message_id_header = email.utils.make_msgid(idstring=token[:16], domain=sender_domain)
    try:
        delivery = await mailer.send(
            config, to=enrollment.email, to_name=enrollment.name, subject=subject, text=text,
            html_body=build_html(body, token=token, tracking=tracking), headers={**headers, "Message-ID": message.message_id_header},
        )
        message.delivery = delivery
        message.sent_at = utcnow()
    except mailer.MailError as exc:
        message.status = "failed"
        message.error = str(exc)[:1000]
        message.delivery = "error"
    db.add(message)
    if message.status == "sent":
        if not enrollment.thread_message_id:
            enrollment.thread_message_id = message.message_id_header
            enrollment.thread_subject = subject
        db.add(
            Activity(
                id=uuid.uuid4(),
                organization_id=org,
                contact_id=record.id if enrollment.record_type == "contact" else None,
                account_id=getattr(record, "account_id", None) if enrollment.record_type == "contact" else None,
                lead_id=record.id if enrollment.record_type == "lead" else None,
                created_by_user_id=enrollment.sender_user_id,
                activity_type="email",
                subject=subject[:250],
                body=body,
                direction="outbound",
                occurred_at=utcnow(),
                activity_metadata={"source": "sequence", "sequence_id": str(enrollment.sequence_id), "step_id": str(step.id), "delivery": message.delivery},
            )
        )
    return message


async def create_task_step(db: AsyncSession, enrollment: SequenceEnrollment, step: SequenceStep, sequence: Sequence) -> SequenceMessage:
    record = await _record(db, enrollment)
    if record is None:
        await finish(db, enrollment, "removed", "Record deleted")
        raise ValueError("The person was deleted.")
    defaults = await workspace_settings.get(db, enrollment.organization_id, "sequences")
    sender = await db.get(User, enrollment.sender_user_id) if enrollment.sender_user_id else None
    organization = await db.get(Organization, enrollment.organization_id)
    token = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    subject = await _render(db, record, sender, organization, step.subject, token) if step.subject else None
    body = await _render(db, record, sender, organization, step.body, token) if step.body else None
    note = await _render(db, record, sender, organization, step.note, token) if step.note else None
    label = STEP_KINDS.get(step.kind, "Task")
    title = {
        "manual_email": f"Email {enrollment.name}",
        "call": f"Call {enrollment.name}",
        "linkedin": f"LinkedIn: connect with {enrollment.name}",
    }.get(step.kind, f"{note.splitlines()[0][:80] if note else 'Follow up with'} · {enrollment.name}" if note else f"Follow up with {enrollment.name}")
    phone = getattr(record, "phone", None) or getattr(record, "mobile", None)
    description_parts = [f"{sequence.name} · step {enrollment.current_position + 1} ({label})"]
    if note:
        description_parts.append(note)
    if step.kind == "call" and phone:
        description_parts.append(f"Phone: {phone}")
    if step.kind == "manual_email" and (subject or body):
        description_parts.append(f"Subject: {subject or ''}\n\n{body or ''}")
    task = Task(
        id=uuid.uuid4(),
        organization_id=enrollment.organization_id,
        contact_id=record.id if enrollment.record_type == "contact" else None,
        account_id=getattr(record, "account_id", None) if enrollment.record_type == "contact" else None,
        lead_id=record.id if enrollment.record_type == "lead" else None,
        assigned_to_user_id=enrollment.sender_user_id,
        created_by_user_id=enrollment.enrolled_by_user_id,
        title=title[:250],
        description="\n\n".join(description_parts),
        task_type=TASK_TYPES.get(step.kind, "follow_up"),
        status="open",
        priority=step.priority if step.priority in ("low", "medium", "high", "urgent") else "medium",
        source="sequence",
        due_at=utcnow() + timedelta(hours=defaults.task_due_hours),
        task_metadata={"sequence_enrollment_id": str(enrollment.id), "sequence_id": str(sequence.id), "step_id": str(step.id), "step_kind": step.kind},
    )
    db.add(task)
    await db.flush()
    message = SequenceMessage(
        id=uuid.uuid4(), organization_id=enrollment.organization_id, enrollment_id=enrollment.id, sequence_id=sequence.id, step_id=step.id,
        kind=step.kind, to_email=enrollment.email, subject=subject, body=body or note, status="task_created", token=token, task_id=task.id,
        sender_user_id=enrollment.sender_user_id,
    )
    db.add(message)
    return message


async def advance(db: AsyncSession, enrollment: SequenceEnrollment, steps: list[SequenceStep]) -> None:
    enrollment.current_position += 1
    if enrollment.current_position >= len(steps):
        await finish(db, enrollment, "finished", "Completed every step")
        return
    enrollment.next_run_at = utcnow() + step_delay(steps[enrollment.current_position])


async def process_enrollment(
    db: AsyncSession,
    enrollment: SequenceEnrollment,
    sequence: Sequence,
    steps: list[SequenceStep],
    protection: workspace_settings.SendingProtection,
    stats_cache: dict[Any, dict[str, Any]],
) -> str:
    now = utcnow()
    if enrollment.current_position >= len(steps):
        await finish(db, enrollment, "finished", "Completed every step")
        return "finished"
    step = steps[enrollment.current_position]
    if step.kind == "auto_email":
        if not in_window(protection, now):
            enrollment.next_run_at = next_window(protection, now)
            return "outside_window"
        if enrollment.email and await suppressed(db, enrollment.organization_id, enrollment.email):
            await finish(db, enrollment, "unsubscribed", "On the do-not-contact list")
            return "suppressed"
        record = await _record(db, enrollment)
        if record is not None and getattr(record, "email_opt_out", False):
            await finish(db, enrollment, "unsubscribed", "Unsubscribed")
            return "unsubscribed"
        key = (enrollment.organization_id, enrollment.sender_user_id)
        if key not in stats_cache:
            stats_cache[key] = await mailbox_stats(db, enrollment.organization_id, enrollment.sender_user_id, protection)
        stats = stats_cache[key]
        if stats["paused"]:
            enrollment.next_run_at = now + timedelta(hours=6)
            enrollment.error = f"Sending paused: this mailbox's bounce rate is {stats['bounce_rate']}%."
            return "bounce_paused"
        if stats["sent_today"] >= stats["daily_limit"]:
            enrollment.next_run_at = next_window(protection, now + timedelta(days=1) - timedelta(hours=now.hour))
            return "daily_limit"
        if stats["sent_last_hour"] >= stats["hourly_limit"]:
            enrollment.next_run_at = now + timedelta(minutes=15)
            return "hourly_limit"
        last = stats.get("last_sent_at")
        if last and (now - last).total_seconds() < protection.min_seconds_between:
            enrollment.next_run_at = last + timedelta(seconds=protection.min_seconds_between)
            return "spacing"
        try:
            message = await send_email_step(db, enrollment, step)
        except ValueError:
            return "removed"
        if message.status == "failed":
            enrollment.error = message.error
            enrollment.next_run_at = now + timedelta(hours=1)
            attempts = (
                await db.execute(
                    select(func.count()).select_from(SequenceMessage).where(
                        SequenceMessage.enrollment_id == enrollment.id, SequenceMessage.step_id == step.id, SequenceMessage.status == "failed"
                    )
                )
            ).scalar_one()
            if attempts + 1 >= 3:
                await finish(db, enrollment, "failed", f"Couldn't send: {message.error}"[:40])
            return "failed"
        stats["sent_today"] += 1
        stats["sent_last_hour"] += 1
        stats["last_sent_at"] = now
        enrollment.error = None
        await advance(db, enrollment, steps)
        return "sent"
    # Manual step: create a task and wait until it's done.
    try:
        await create_task_step(db, enrollment, step, sequence)
    except ValueError:
        return "removed"
    enrollment.next_run_at = None
    return "task_created"


async def check_waiting(db: AsyncSession, limit: int = 300) -> int:
    """Enrollments waiting on a manual task move on once the task is done."""

    rows = (
        await db.execute(
            select(SequenceEnrollment, SequenceMessage, Task)
            .join(SequenceMessage, SequenceMessage.enrollment_id == SequenceEnrollment.id)
            .join(Task, Task.id == SequenceMessage.task_id)
            .where(
                SequenceEnrollment.status == "active",
                SequenceEnrollment.next_run_at.is_(None),
                SequenceMessage.status == "task_created",
                Task.status.in_(("completed", "cancelled")),
            )
            .limit(limit)
        )
    ).all()
    moved = 0
    for enrollment, message, task in rows:
        message.status = "completed" if task.status == "completed" else "skipped"
        message.sent_at = task.completed_at or utcnow()
        steps = await active_steps(db, enrollment.sequence_id)
        await advance(db, enrollment, steps)
        moved += 1
    return moved


async def process_due(db: AsyncSession, limit: int = 200) -> int:
    """Scheduler job: run every enrollment whose next step is due."""

    db.sync_session.info["automation_disabled"] = True
    moved = await check_waiting(db)
    now = utcnow()
    rows = (
        await db.execute(
            select(SequenceEnrollment, Sequence)
            .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
            .where(
                SequenceEnrollment.status == "active",
                SequenceEnrollment.next_run_at.is_not(None),
                SequenceEnrollment.next_run_at <= now,
                Sequence.status == "active",
            )
            .order_by(SequenceEnrollment.next_run_at)
            .limit(limit)
        )
    ).all()
    protection_cache: dict[uuid.UUID, workspace_settings.SendingProtection] = {}
    steps_cache: dict[uuid.UUID, list[SequenceStep]] = {}
    stats_cache: dict[Any, dict[str, Any]] = {}
    from app.services.billing import get_subscription, plan_state

    allowed: dict[uuid.UUID, bool] = {}
    for enrollment, sequence in rows:
        org = enrollment.organization_id
        if org not in allowed:
            state = plan_state(await get_subscription(db, org))
            allowed[org] = "sequences" in state.features and not state.read_only
        if not allowed[org]:
            continue
        if org not in protection_cache:
            protection_cache[org] = await workspace_settings.get(db, org, "sending")
        if sequence.id not in steps_cache:
            steps_cache[sequence.id] = await active_steps(db, sequence.id)
        try:
            await process_enrollment(db, enrollment, sequence, steps_cache[sequence.id], protection_cache[org], stats_cache)
            moved += 1
        except Exception:  # noqa: BLE001 - one bad enrollment mustn't stop the rest
            logger.exception("Sequence enrollment %s failed", enrollment.id)
            enrollment.error = "Unexpected error; AIVA will retry."
            enrollment.next_run_at = now + timedelta(minutes=30)
    await db.commit()
    db.sync_session.info["automation_disabled"] = False
    return moved


# ---------------------------------------------------------------------------
# Replies, bounces, meetings and unsubscribes
# ---------------------------------------------------------------------------


async def handle_inbound(db: AsyncSession, organization_id: uuid.UUID, message: Any) -> str | None:
    """Called for each new email pulled from a mailbox."""

    from_address = (message.from_address or "").lower()
    subject = message.subject or ""
    if any(marker in from_address for marker in BOUNCE_SENDERS) or BOUNCE_SUBJECT.search(subject):
        addresses = {a.lower() for a in EMAIL_IN_TEXT.findall(message.text or "")} - {from_address}
        if not addresses:
            return None
        enrollments = (
            await db.execute(
                select(SequenceEnrollment).where(
                    SequenceEnrollment.organization_id == organization_id,
                    SequenceEnrollment.email.in_(addresses),
                    SequenceEnrollment.status.in_(("active", "paused", "finished")),
                )
            )
        ).scalars().all()
        defaults = await workspace_settings.get(db, organization_id, "sequences")
        for enrollment in enrollments:
            sent = (
                await db.execute(
                    select(SequenceMessage)
                    .where(SequenceMessage.enrollment_id == enrollment.id, SequenceMessage.status == "sent")
                    .order_by(SequenceMessage.sent_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if sent is not None and sent.bounced_at is None:
                sent.bounced_at = utcnow()
            # Settings → Sequences "stop when an email bounces": when off, the bounce is recorded
            # (and counts toward the mailbox's bounce rate) but the person stays in the sequence.
            if not defaults.stop_on_bounce:
                continue
            if enrollment.status != "finished":
                await finish(db, enrollment, "bounced", "Email bounced")
            await _suppress(db, organization_id, enrollment.email, "bounced")
        return "bounce" if enrollments else None

    enrollments = (
        await db.execute(
            select(SequenceEnrollment).where(
                SequenceEnrollment.organization_id == organization_id,
                SequenceEnrollment.email == from_address,
                SequenceEnrollment.status.in_(("active", "paused")),
            )
        )
    ).scalars().all()
    if not enrollments:
        return None
    for enrollment in enrollments:
        sequence = await db.get(Sequence, enrollment.sequence_id)
        stop = (sequence.settings or {}).get("stop_on_reply")
        defaults = await workspace_settings.get(db, organization_id, "sequences")
        last = (
            await db.execute(
                select(SequenceMessage)
                .where(SequenceMessage.enrollment_id == enrollment.id, SequenceMessage.status == "sent")
                .order_by(SequenceMessage.sent_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last.replied_at is None:
            last.replied_at = message.date or utcnow()
        if stop if stop is not None else defaults.stop_on_reply:
            await finish(db, enrollment, "replied", "Replied")
            if enrollment.sender_user_id:
                db.add(
                    Notification(
                        id=uuid.uuid4(), organization_id=organization_id, user_id=enrollment.sender_user_id, kind="sequence",
                        title=f"{enrollment.name} replied"[:200], body=f"“{subject[:120]}” · {sequence.name if sequence else 'Sequence'} stopped for them.",
                        link=f"/sequences/{enrollment.sequence_id}", is_read=False,
                    )
                )
    return "reply"


async def _suppress(db: AsyncSession, organization_id: uuid.UUID, email: str | None, reason: str) -> None:
    if not email:
        return
    exists = (
        await db.execute(
            select(SuppressionEntry.id).where(SuppressionEntry.organization_id == organization_id, SuppressionEntry.kind == "email", SuppressionEntry.value == email.lower())
        )
    ).first()
    if not exists:
        db.add(SuppressionEntry(id=uuid.uuid4(), organization_id=organization_id, kind="email", value=email.lower(), reason=reason))


async def meeting_booked(db: AsyncSession, organization_id: uuid.UUID, emails: set[str]) -> int:
    emails = {e.lower() for e in emails if e}
    if not emails:
        return 0
    rows = (
        await db.execute(
            select(SequenceEnrollment, Sequence)
            .join(Sequence, Sequence.id == SequenceEnrollment.sequence_id)
            .where(SequenceEnrollment.organization_id == organization_id, SequenceEnrollment.email.in_(emails), SequenceEnrollment.status.in_(("active", "paused")))
        )
    ).all()
    defaults = await workspace_settings.get(db, organization_id, "sequences")
    count = 0
    for enrollment, sequence in rows:
        stop = (sequence.settings or {}).get("stop_on_meeting")
        if stop if stop is not None else defaults.stop_on_meeting:
            await finish(db, enrollment, "meeting_booked", "Meeting booked")
            count += 1
    return count


async def unsubscribed(db: AsyncSession, message: SequenceMessage) -> None:
    enrollment = await db.get(SequenceEnrollment, message.enrollment_id)
    if enrollment is not None and enrollment.status in {"active", "paused"}:
        await finish(db, enrollment, "unsubscribed", "Unsubscribed")
    await _suppress(db, message.organization_id, message.to_email, "unsubscribed")


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

DOH = ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve")
COMMON_SELECTORS = ("google", "selector1", "selector2", "default", "k1", "s1", "s2", "mail", "dkim", "zoho", "mxvault")


async def dns_lookup(name: str, record_type: str) -> list[str]:
    last_error: Exception | None = None
    for endpoint in DOH:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(endpoint, params={"name": name, "type": record_type}, headers={"Accept": "application/dns-json"})
            if response.status_code >= 400:
                continue
            data = response.json()
            answers = data.get("Answer") or []
            return [str(a.get("data", "")).strip().strip('"').replace('" "', "") for a in answers if a.get("data")]
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"DNS lookup failed ({last_error.__class__.__name__ if last_error else 'no answer'}).")


def _check(status_value: str, found: list[str] | str | None, detail: str, fix: str | None = None) -> dict[str, Any]:
    return {"status": status_value, "found": found, "detail": detail, "fix": fix}


async def check_domain(domain: str, selector: str | None, tracking_host: str | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        txt = await dns_lookup(domain, "TXT")
    except RuntimeError as exc:
        return {"error": str(exc)}
    spf = [t for t in txt if t.lower().startswith("v=spf1")]
    if not spf:
        checks["spf"] = _check("fail", None, "No SPF record, so receivers can't tell which servers may send for you.",
                               f'Add a TXT record on {domain}: v=spf1 include:_spf.google.com ~all (Google) or include:spf.protection.outlook.com (Microsoft 365).')
    elif len(spf) > 1:
        checks["spf"] = _check("warn", spf, "There's more than one SPF record; receivers treat that as an error.", "Merge them into a single v=spf1 record.")
    else:
        weak = spf[0].strip().endswith("+all")
        checks["spf"] = _check("warn" if weak else "pass", spf, "SPF allows any server (+all)." if weak else "SPF record found.", "End the record with ~all or -all." if weak else None)

    selectors = [selector] if selector else list(COMMON_SELECTORS)
    dkim_found = None
    for candidate in selectors:
        try:
            values = await dns_lookup(f"{candidate}._domainkey.{domain}", "TXT")
        except RuntimeError:
            continue
        hit = [v for v in values if "v=dkim1" in v.lower() or "p=" in v.lower()]
        if hit:
            dkim_found = (candidate, hit)
            break
    if dkim_found:
        checks["dkim"] = _check("pass", [f"{dkim_found[0]}._domainkey: {dkim_found[1][0][:80]}…"], f"DKIM key found (selector “{dkim_found[0]}”).")
    else:
        checks["dkim"] = _check(
            "fail", None,
            f"No DKIM key found{f' for selector “{selector}”' if selector else ' for common selectors'}.",
            "Turn on DKIM signing in Google Workspace (Apps → Gmail → Authenticate email) or Microsoft 365 (Defender → DKIM), then publish the TXT record they give you.",
        )
    try:
        dmarc = [v for v in await dns_lookup(f"_dmarc.{domain}", "TXT") if v.lower().startswith("v=dmarc1")]
    except RuntimeError:
        dmarc = []
    if not dmarc:
        checks["dmarc"] = _check("fail", None, "No DMARC policy. Gmail and Yahoo require one for bulk senders.",
                                 f"Add a TXT record on _dmarc.{domain}: v=DMARC1; p=none; rua=mailto:dmarc@{domain}")
    else:
        policy = re.search(r"p=(\w+)", dmarc[0])
        value = policy.group(1).lower() if policy else "none"
        checks["dmarc"] = _check("pass" if value in ("quarantine", "reject") else "warn", dmarc,
                                 f"DMARC policy is “{value}”.",
                                 "Once SPF and DKIM pass, move to p=quarantine for stronger protection." if value == "none" else None)
    try:
        mx = await dns_lookup(domain, "MX")
    except RuntimeError:
        mx = []
    checks["mx"] = _check("pass" if mx else "fail", mx or None, "Mail servers found, so replies can arrive." if mx else "No MX records, so replies to this domain will bounce.",
                          None if mx else "Add the MX records from your email provider.")
    if tracking_host:
        app_host = urlsplit(get_settings().app_base_url).hostname or ""
        try:
            cname = await dns_lookup(tracking_host, "CNAME")
        except RuntimeError:
            cname = []
        ok = any(c.rstrip(".").lower() == app_host.lower() for c in cname)
        checks["tracking"] = _check("pass" if ok else "fail", cname or None,
                                    "Tracking domain points to AIVA." if ok else f"{tracking_host} doesn't point to {app_host} yet.",
                                    None if ok else f"Add a CNAME record: {tracking_host} → {app_host}, then add it as a custom domain on your AIVA host.")
    return checks


def domain_status(checks: dict[str, Any]) -> str:
    if checks.get("error"):
        return "pending"
    core = [checks.get(k, {}).get("status") for k in ("spf", "dkim", "dmarc")]
    if all(s == "pass" for s in core) and checks.get("mx", {}).get("status") == "pass":
        return "verified"
    return "issues"


# ---------------------------------------------------------------------------
# Content checks (Diagnose)
# ---------------------------------------------------------------------------


def content_issues(subject: str | None, body: str | None) -> list[str]:
    issues = []
    subject = subject or ""
    body = body or ""
    lowered = f"{subject} {body}".lower()
    words = [w for w in SPAM_WORDS if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", lowered)]
    if words:
        issues.append(f"Spam-trigger words: {', '.join(words[:5])}")
    if subject and sum(1 for c in subject if c.isupper()) > max(8, len(subject) * 0.5):
        issues.append("Subject is mostly capitals")
    if len(subject) > 70:
        issues.append("Subject is longer than 70 characters")
    links = LINK_RE.findall(body)
    if len(links) > 3:
        issues.append(f"{len(links)} links (keep cold emails to 1–2)")
    if body.count("!") > 3:
        issues.append("Lots of exclamation marks")
    if len(body.split()) > 250:
        issues.append("Longer than 250 words")
    if "{{" in subject + body and "}}" not in subject + body:
        issues.append("A merge field isn't closed with }}")
    return issues


__all__ = [
    "STEP_KINDS", "FINISHED", "enroll", "finish", "process_due", "handle_inbound", "meeting_booked", "unsubscribed",
    "check_domain", "domain_status", "content_issues", "mailbox_stats", "in_window", "next_window", "click_signature",
    "send_email_step", "active_steps",
]
