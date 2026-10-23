"""Background jobs: reminders, campaigns, journeys, chat alerts and AI agents.

One asyncio task runs inside the API process. A PostgreSQL advisory lock
makes sure only one process runs the jobs when several API workers are up.
Each job uses its own session so one failure doesn't affect the others.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal, engine
from app.models.platform import Notification
from app.models.task import Task
from app.models.user import User

logger = logging.getLogger("aiva.scheduler")

LOCK_KEY = 7_311_2026


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def send_due_reminders(db) -> int:
    """Notify assignees (in the app, and by email when mail is set up) when a task reminder is due."""

    from app.services import mailer

    now = utcnow()
    tasks = (
        await db.execute(
            select(Task)
            .where(
                Task.reminder_at.is_not(None),
                Task.reminder_at <= now,
                Task.reminder_at >= now - timedelta(days=3),
                Task.status.in_(("open", "in_progress")),
                Task.assigned_to_user_id.is_not(None),
                Task.task_metadata["reminder_sent_at"].astext.is_(None),
            )
            .limit(100)
        )
    ).scalars().all()
    db.sync_session.info["automation_disabled"] = True
    for task in tasks:
        due = f" (due {task.due_at:%b %d, %H:%M} UTC)" if task.due_at else ""
        db.add(
            Notification(
                id=uuid.uuid4(),
                organization_id=task.organization_id,
                user_id=task.assigned_to_user_id,
                kind="reminder",
                title=f"Reminder: {task.title}"[:200],
                body=f"Follow-up reminder{due}.",
                link="/tasks",
                is_read=False,
            )
        )
        delivery = "in_app"
        user = await db.get(User, task.assigned_to_user_id)
        config = await mailer.sender_config(db, task.organization_id, None)
        if user is not None and config is not None:
            try:
                await mailer.send(
                    config,
                    to=user.email,
                    to_name=user.full_name,
                    subject=f"Reminder: {task.title}"[:200],
                    text=f"Hi {user.full_name.split(' ')[0]},\n\nThis is your AIVA reminder for “{task.title}”{due}.\n\n{get_settings().app_base_url.rstrip('/')}/tasks",
                )
                delivery = "email"
            except mailer.MailError as exc:
                logger.warning("Reminder email failed: %s", exc)
        task.task_metadata = {**(task.task_metadata or {}), "reminder_sent_at": now.isoformat(), "reminder_delivery": delivery}
    if tasks:
        await db.commit()
    return len(tasks)


async def _job_campaigns(db) -> int:
    from app.services import campaigns

    started = await campaigns.start_scheduled(db)
    return started + await campaigns.deliver(db)


async def _job_journeys(db) -> int:
    from app.services import journeys

    return await journeys.run_due(db)


async def _job_chat(db) -> int:
    from app.services import chat

    return await chat.deliver_pending(db)


async def _job_agents(db) -> int:
    from app.services import ai_agents

    return await ai_agents.run_scheduled(db)


async def _job_mailbox_sync(db) -> int:
    from app.services import mailbox_sync

    return await mailbox_sync.sync_due(db)


async def _job_meeting_reminders(db) -> int:
    from app.api.routes.meetings import send_meeting_reminders
    from app.api.routes.work import send_appointment_reminders

    return await send_meeting_reminders(db) + await send_appointment_reminders(db)


async def _job_social(db) -> int:
    from app.api.routes.engagement import publish_due_posts

    return await publish_due_posts(db)


JOBS = (
    ("reminders", send_due_reminders),
    ("meeting_reminders", _job_meeting_reminders),
    ("social_posts", _job_social),
    ("campaigns", _job_campaigns),
    ("journeys", _job_journeys),
    ("chat", _job_chat),
    ("agents", _job_agents),
    ("mailbox_sync", _job_mailbox_sync),
)


async def run_once() -> dict[str, int]:
    results: dict[str, int] = {}
    async with engine.connect() as lock_connection:
        got = (await lock_connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})).scalar()
        if not got:
            return results
        try:
            for name, job in JOBS:
                async with AsyncSessionLocal() as db:
                    try:
                        results[name] = await job(db)
                    except Exception:  # noqa: BLE001
                        logger.exception("Background job %s failed", name)
                        await db.rollback()
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            await lock_connection.commit()
    return results


class Scheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop = asyncio.Event()
            self._task = asyncio.create_task(self._loop(), name="aiva-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        interval = max(10, get_settings().scheduler_interval_seconds)
        await asyncio.sleep(3)
        while not self._stop.is_set():
            try:
                await run_once()
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler run failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


scheduler = Scheduler()
