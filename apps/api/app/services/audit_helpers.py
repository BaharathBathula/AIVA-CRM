"""Audit helpers for authentication events."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import client_ip
from app.models.user import User
from app.services.automation import record_audit


def audit_auth_event(
    db: AsyncSession,
    organization_id: uuid.UUID,
    user: User,
    action: str,
    request: Request,
    extra: dict[str, Any] | None = None,
) -> None:
    record_audit(
        db,
        organization_id=organization_id,
        action=action,
        entity_type="user",
        entity_id=user.id,
        entity_label=user.full_name or user.email,
        changes=extra or {},
        actor_user_id=user.id,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
    )
