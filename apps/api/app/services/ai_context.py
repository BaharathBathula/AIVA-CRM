"""The AI Context Center: what AIVA's AI knows about your company.

Admins describe the company, products, ideal customer, competitors, tone of
voice and FAQs once (Settings → Ideal customer profile → AI Context Center);
every AI feature that's switched on for it gets that context in its system
prompt, so drafts and summaries sound like the team and stay accurate.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import ai, workspace_settings

# Set while AI agents run so their drafts use the workspace's context.
agent_system_prompt: ContextVar[str | None] = ContextVar("agent_system_prompt", default=None)


def build_prompt(context: workspace_settings.AiContextSettings, company_name: str | None = None) -> str:
    parts: list[str] = []
    if company_name:
        parts.append(f"You work for {company_name}.")
    if context.company_overview.strip():
        parts.append(f"About the company: {context.company_overview.strip()}")
    if context.products.strip():
        parts.append(f"Products and services: {context.products.strip()}")
    if context.value_props.strip():
        parts.append(f"Why customers choose us: {context.value_props.strip()}")
    if context.ideal_customer.strip():
        parts.append(f"Ideal customer: {context.ideal_customer.strip()}")
    if context.competitors:
        lines = "; ".join(f"{c.name}{f' (how we differ: {c.differentiator.strip()})' if c.differentiator.strip() else ''}" for c in context.competitors)
        parts.append(f"Competitors: {lines}")
    if context.tone.strip():
        parts.append(f"Tone of voice: {context.tone.strip()}")
    if context.words_to_avoid:
        parts.append(f"Never use these words or phrases: {', '.join(context.words_to_avoid)}.")
    if context.faqs:
        faq = " ".join(f"Q: {f.question.strip()} A: {f.answer.strip()}" for f in context.faqs if f.answer.strip())
        if faq:
            parts.append(f"Approved answers to common questions: {faq}")
    if context.signature.strip():
        parts.append(f"Sign emails with:\n{context.signature.strip()}")
    return "\n".join(parts)


async def system_prompt(db: AsyncSession, organization_id: uuid.UUID, use: str) -> str:
    """AIVA's base instructions plus the company context, when it's enabled for ``use``."""

    from app.models.organization import Organization

    context = await workspace_settings.get(db, organization_id, "ai_context")
    if use not in context.use_for:
        return ai.SYSTEM
    organization = await db.get(Organization, organization_id)
    extra = build_prompt(context, organization.name if organization else None)
    return f"{ai.SYSTEM}\n\nCompany context (use it, don't recite it):\n{extra}" if extra else ai.SYSTEM


def apply_words_to_avoid(text: str, context: workspace_settings.AiContextSettings) -> str:
    """Rules-based drafts honour the banned-phrase list too."""

    import re

    for phrase in context.words_to_avoid:
        phrase = phrase.strip()
        if phrase:
            text = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", text)
