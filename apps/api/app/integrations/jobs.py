"""Scheduler job: lets every provider module do its periodic work."""

from __future__ import annotations

import importlib
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.providers import ALL_MODULES

logger = logging.getLogger("aiva.integrations")


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
    return handled
