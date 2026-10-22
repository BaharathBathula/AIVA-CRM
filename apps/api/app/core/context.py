"""Per-request context shared with database event hooks (audit + automation).

The authenticated dependency stores the acting user on ``session.info`` so
SQLAlchemy flush events can attribute changes. Context variables mirror the
same values for code paths that do not have the session at hand.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

current_actor_id: ContextVar[uuid.UUID | None] = ContextVar("aiva_actor_id", default=None)
current_organization_id: ContextVar[uuid.UUID | None] = ContextVar("aiva_organization_id", default=None)
current_ip: ContextVar[str | None] = ContextVar("aiva_ip", default=None)
current_user_agent: ContextVar[str | None] = ContextVar("aiva_user_agent", default=None)


def bind_session_context(
    session,
    *,
    actor_id: uuid.UUID | None,
    organization_id: uuid.UUID | None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    info = session.info if hasattr(session, "info") else {}
    info["actor_user_id"] = actor_id
    info["organization_id"] = organization_id
    info["ip"] = ip
    info["user_agent"] = (user_agent or "")[:300] or None
    current_actor_id.set(actor_id)
    current_organization_id.set(organization_id)
    current_ip.set(ip)
    current_user_agent.set(info["user_agent"])
