"""Scheduler job: lets every provider module do its periodic work."""

from __future__ import annotations

import importlib
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers import ALL_MODULES
from app.models.integration_hub import IntegrationLog, IntegrationOAuthState

logger = logging.getLogger("aiva.integrations")

# Activity older than this is removed; sign-in attempts are kept for a day.
LOG_RETENTION_DAYS = 400
_last_cleanup = 0.0


async def cleanup(db: AsyncSession) -> int:
    """Hourly at most: drop old activity entries and expired sign-in attempts."""
    global _last_cleanup
    if time.monotonic() - _last_cleanup < 3600:
        return 0
    _last_cleanup = time.monotonic()
    now = datetime.now(timezone.utc)
    logs = await db.execute(delete(IntegrationLog).where(IntegrationLog.created_at < now - timedelta(days=LOG_RETENTION_DAYS)))
    states = await db.execute(delete(IntegrationOAuthState).where(IntegrationOAuthState.expires_at < now - timedelta(days=1)))
    await db.commit()
    return int(logs.rowcount or 0) + int(states.rowcount or 0)


async def run_due(db: AsyncSession) -> int:
    handled = 0
    for name in ALL_MODULES:
        module = importlib.import_module(f"app.integrations.providers.{name}")
        function = getattr(module, "run_due", None)
        if function is None:
            continue
        try:
            handled += int(await function(db) or 0)
        except Exception:  # noqa: BLE001 - one provider must not stop the others
            logger.exception("Integration job %s failed", name)
            await db.rollback()
    try:
        handled += await cleanup(db)
    except Exception:  # noqa: BLE001
        logger.exception("Integration cleanup failed")
        await db.rollback()
    return handled
