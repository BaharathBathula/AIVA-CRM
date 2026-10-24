"""Integration endpoints: mail_calendar (see app/integrations/providers for the provider logic)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])
