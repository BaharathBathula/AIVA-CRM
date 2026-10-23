"""Slack and Microsoft Teams alerts through incoming webhooks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models.editions import Integration, OutboundMessage

logger = logging.getLogger("aiva.chat")

MAX_ATTEMPTS = 5


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def absolute(link: str | None) -> str | None:
    if not link:
        return None
    if link.startswith("http"):
        return link
    return f"{get_settings().app_base_url.rstrip('/')}{link}"


def slack_payload(title: str, text: str, link: str | None) -> dict[str, Any]:
    url = absolute(link)
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{text}"}},
    ]
    if url:
        blocks.append(
            {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open in AIVA"}, "url": url}]}
        )
    return {"text": f"{title}: {text}", "blocks": blocks}


def teams_payload(title: str, text: str, link: str | None) -> dict[str, Any]:
    url = absolute(link)
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": text, "wrap": True},
        ],
    }
    if url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in AIVA", "url": url}]
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }


async def post(channel: str, webhook_url: str, title: str, text: str, link: str | None = None) -> None:
    payload = slack_payload(title, text, link) if channel == "slack" else teams_payload(title, text, link)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(webhook_url, json=payload)
    if response.status_code >= 300:
        raise RuntimeError(f"{channel.title()} answered {response.status_code}: {response.text[:200]}")


def webhook_url(integration: Integration) -> str | None:
    return decrypt((integration.config or {}).get("webhook_url"))


async def deliver_pending(db: AsyncSession, limit: int = 50) -> int:
    now = utcnow()
    messages = (
        await db.execute(
            select(OutboundMessage)
            .where(
                OutboundMessage.status == "pending",
                (OutboundMessage.deliver_after.is_(None)) | (OutboundMessage.deliver_after <= now),
            )
            .order_by(OutboundMessage.created_at)
            .limit(limit)
        )
    ).scalars().all()
    if not messages:
        return 0
    integrations: dict[tuple, Integration | None] = {}
    for message in messages:
        key = (message.organization_id, message.channel)
        if key not in integrations:
            integrations[key] = (
                await db.execute(
                    select(Integration).where(
                        Integration.organization_id == message.organization_id,
                        Integration.kind == message.channel,
                        Integration.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        integration = integrations[key]
        url = webhook_url(integration) if integration else None
        if not url:
            message.status = "failed"
            message.last_error = f"{message.channel.title()} isn't connected."
            continue
        payload = message.payload or {}
        try:
            await post(message.channel, url, payload.get("title") or "AIVA", payload.get("text") or "", payload.get("link"))
            message.status = "sent"
            message.sent_at = utcnow()
            integration.last_used_at = utcnow()
            integration.last_error = None
        except Exception as exc:  # noqa: BLE001
            message.attempts += 1
            message.last_error = str(exc)[:500]
            if integration is not None:
                integration.last_error = message.last_error
            if message.attempts >= MAX_ATTEMPTS:
                message.status = "failed"
            else:
                message.deliver_after = utcnow() + timedelta(minutes=2 ** message.attempts)
            logger.warning("Chat delivery failed: %s", exc)
    await db.commit()
    return len(messages)
