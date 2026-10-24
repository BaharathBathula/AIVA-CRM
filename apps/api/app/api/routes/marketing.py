"""Marketing: email templates, campaigns, web forms, journeys, ABM and Google Ads."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, get_current_principal, require_feature, require_permission
from app.core.config import get_settings
from app.core.crypto import encrypt, new_token
from app.core.plans import plan_for
from app.db.session import get_db
from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.editions import (
    CampaignRecipient,
    EmailCampaign,
    EmailTemplate,
    Integration,
    Journey,
    JourneyEnrollment,
    TargetAccount,
    WebForm,
    WebFormSubmission,
)
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from app.services import campaigns as campaign_service
from app.services import journeys as journey_service
from app.services import mailer
from app.services.automation import invalidate_automation_cache, record_audit
from app.services.billing import enforce_limit
from app.services.forms import CASE_FIELDS, INPUT_TYPES, LEAD_FIELDS, default_fields, new_slug, validate_fields
from app.services.lookups import names_for
from app.services.merge import MERGE_FIELDS, record_context, render, text_to_html

router = APIRouter(tags=["Marketing"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# Email templates
# ===========================================================================


class TemplatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=50000)
    category: str | None = Field(default=None, max_length=80)
    # Left out, new templates follow Settings → Team sharing & defaults.
    is_shared: bool | None = None


def template_out(template: EmailTemplate, owners: dict[str, str] | None = None) -> dict:
    return {
        "id": str(template.id),
        "name": template.name,
        "subject": template.subject,
        "body": template.body,
        "category": template.category,
        "is_shared": template.is_shared,
        "owner_user_id": str(template.owner_user_id) if template.owner_user_id else None,
        "owner_name": (owners or {}).get(str(template.owner_user_id)),
        "usage_count": template.usage_count,
        "updated_at": template.updated_at,
    }


@router.get("/email-templates/merge-fields")
async def merge_fields(_: Principal = Depends(get_current_principal)):
    return [{"key": key, "label": label, "token": "{{" + key + "}}"} for key, label in MERGE_FIELDS]


@router.get("/email-templates", dependencies=[Depends(require_feature("email_templates"))])
async def list_templates(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    templates = (
        await db.execute(
            select(EmailTemplate)
            .where(
                EmailTemplate.organization_id == principal.organization_id,
                (EmailTemplate.is_shared.is_(True)) | (EmailTemplate.owner_user_id == principal.user_id),
            )
            .order_by(EmailTemplate.category.nullsfirst(), EmailTemplate.name)
        )
    ).scalars().all()
    owners = await names_for(db, principal.organization_id, "user", [t.owner_user_id for t in templates])
    return [template_out(t, owners) for t in templates]


@router.post("/email-templates", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("email_templates"))])
async def create_template(
    payload: TemplatePayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    from app.services import workspace_settings

    data = payload.model_dump()
    if data["is_shared"] is None:
        data["is_shared"] = (await workspace_settings.get(db, principal.organization_id, "sharing")).templates_shared
    template = EmailTemplate(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        owner_user_id=principal.user_id,
        **data,
    )
    db.add(template)
    await db.commit()
    return template_out(template, {str(principal.user_id): principal.full_name})


async def _template(db: AsyncSession, principal: Principal, template_id: uuid.UUID, *, editing: bool = False) -> EmailTemplate:
    template = await db.get(EmailTemplate, template_id)
    if template is None or template.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    if not template.is_shared and template.owner_user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    if editing and template.owner_user_id != principal.user_id and not principal.can("marketing.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the template's owner or a marketing manager can change it.")
    return template


@router.patch("/email-templates/{template_id}")
async def update_template(
    template_id: uuid.UUID,
    payload: TemplatePayload,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    template = await _template(db, principal, template_id, editing=True)
    for key, value in payload.model_dump(exclude_none=False).items():
        if key == "is_shared" and value is None:
            continue
        setattr(template, key, value)
    await db.commit()
    return template_out(template)


@router.delete("/email-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.write")),
    db: AsyncSession = Depends(get_db),
):
    template = await _template(db, principal, template_id, editing=True)
    await db.delete(template)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RenderPayload(BaseModel):
    record_type: str = Field(pattern="^(lead|contact)$")
    record_id: uuid.UUID


@router.post("/email-templates/{template_id}/render")
async def render_template_for(
    template_id: uuid.UUID,
    payload: RenderPayload,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    template = await _template(db, principal, template_id)
    model = Lead if payload.record_type == "lead" else Contact
    record = await db.get(model, payload.record_id)
    if record is None or record.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found.")
    context = await record_context(db, record, sender=await db.get(User, principal.user_id))
    template.usage_count = (template.usage_count or 0) + 1
    await db.commit()
    return {"subject": render(template.subject, context), "body": render(template.body, context), "to": record.email}


class PreviewPayload(BaseModel):
    subject: str = ""
    body: str = ""


@router.post("/email-templates/preview")
async def preview_text(
    payload: PreviewPayload,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    sample = {
        "first_name": "Jordan",
        "last_name": "Rivera",
        "full_name": "Jordan Rivera",
        "email": "jordan@example.com",
        "company": "Example Co",
        "job_title": "Operations Director",
        "phone": "+1 214-555-0100",
        "owner_name": principal.full_name,
        "owner_email": principal.email,
        "sender_name": principal.full_name,
        "organization_name": (await db.get(Organization, principal.organization_id)).name,
        "today": datetime.now().date(),
        "unsubscribe_url": f"{get_settings().app_base_url.rstrip('/')}/u/example",
        "cf": {},
    }
    return {"subject": render(payload.subject, sample), "body": render(payload.body, sample)}


# ===========================================================================
# Campaigns
# ===========================================================================


class Audience(BaseModel):
    object_type: str = Field(default="contact", pattern="^(lead|contact)$")
    filters: list[dict[str, Any]] = Field(default_factory=list)
    abm_tier: str | None = Field(default=None, pattern="^(all|tier_1|tier_2|tier_3)$")


class CampaignPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=50000)
    template_id: uuid.UUID | None = None
    audience: Audience = Field(default_factory=Audience)
    from_name: str | None = Field(default=None, max_length=200)
    reply_to: str | None = Field(default=None, max_length=320)


async def campaign_out(db: AsyncSession, campaign: EmailCampaign, *, detail: bool = False) -> dict:
    data = {
        "id": str(campaign.id),
        "name": campaign.name,
        "subject": campaign.subject,
        "status": campaign.status,
        "audience": campaign.audience or {},
        "template_id": str(campaign.template_id) if campaign.template_id else None,
        "scheduled_at": campaign.scheduled_at,
        "started_at": campaign.started_at,
        "completed_at": campaign.completed_at,
        "stats": campaign.stats or {},
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }
    if detail:
        data["body"] = campaign.body
        data["from_name"] = campaign.from_name
        data["reply_to"] = campaign.reply_to
    return data


def _campaign_guard(principal: Principal) -> None:
    if not principal.has("mass_email"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Mass email is part of every paid plan.")


@router.get("/campaigns", dependencies=[Depends(require_feature("mass_email"))])
async def list_campaigns(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    campaigns = (
        await db.execute(
            select(EmailCampaign).where(EmailCampaign.organization_id == principal.organization_id).order_by(EmailCampaign.created_at.desc())
        )
    ).scalars().all()
    return [await campaign_out(db, c) for c in campaigns]


async def _apply_template(db: AsyncSession, principal: Principal, payload: CampaignPayload) -> tuple[str, str]:
    subject, body = payload.subject, payload.body
    if payload.template_id and (not subject or not body):
        template = await _template(db, principal, payload.template_id)
        subject = subject or template.subject
        body = body or template.body
    return subject, body


@router.post("/campaigns", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("mass_email"))])
async def create_campaign(
    payload: CampaignPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    subject, body = await _apply_template(db, principal, payload)
    campaign = EmailCampaign(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=payload.name.strip(),
        subject=subject,
        body=body,
        template_id=payload.template_id,
        audience=payload.audience.model_dump(),
        status="draft",
        from_name=payload.from_name,
        reply_to=payload.reply_to,
        created_by_user_id=principal.user_id,
        stats={},
    )
    db.add(campaign)
    await db.commit()
    return await campaign_out(db, campaign, detail=True)


async def _campaign(db: AsyncSession, principal: Principal, campaign_id: uuid.UUID) -> EmailCampaign:
    campaign = await db.get(EmailCampaign, campaign_id)
    if campaign is None or campaign.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campaign not found.")
    return campaign


@router.get("/campaigns/{campaign_id}", dependencies=[Depends(require_feature("mass_email"))])
async def get_campaign(
    campaign_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _campaign(db, principal, campaign_id)
    data = await campaign_out(db, campaign, detail=True)
    sender = await mailer.sender_config(db, principal.organization_id, campaign.created_by_user_id)
    data["delivery"] = sender.source if sender else "outbox"
    return data


@router.patch("/campaigns/{campaign_id}", dependencies=[Depends(require_feature("mass_email"))])
async def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _campaign(db, principal, campaign_id)
    if campaign.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft campaigns can be edited. Duplicate it to send again.")
    subject, body = await _apply_template(db, principal, payload)
    campaign.name = payload.name.strip()
    campaign.subject = subject
    campaign.body = body
    campaign.template_id = payload.template_id
    campaign.audience = payload.audience.model_dump()
    campaign.from_name = payload.from_name
    campaign.reply_to = payload.reply_to
    await db.commit()
    return await campaign_out(db, campaign, detail=True)


@router.post("/campaigns/{campaign_id}/duplicate", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("mass_email"))])
async def duplicate_campaign(
    campaign_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    source = await _campaign(db, principal, campaign_id)
    copy = EmailCampaign(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=f"{source.name} (copy)"[:200],
        subject=source.subject,
        body=source.body,
        template_id=source.template_id,
        audience=source.audience,
        status="draft",
        from_name=source.from_name,
        reply_to=source.reply_to,
        created_by_user_id=principal.user_id,
        stats={},
    )
    db.add(copy)
    await db.commit()
    return await campaign_out(db, copy, detail=True)


@router.delete("/campaigns/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _campaign(db, principal, campaign_id)
    if campaign.status == "sending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the campaign to finish sending.")
    await db.delete(campaign)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/campaigns/audience-preview", dependencies=[Depends(require_feature("mass_email"))])
async def audience_preview(
    payload: Audience,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return await campaign_service.audience_count(db, principal, payload.model_dump())


class SendPayload(BaseModel):
    scheduled_at: datetime | None = None


@router.post("/campaigns/{campaign_id}/send", dependencies=[Depends(require_feature("mass_email"))])
async def send_campaign(
    campaign_id: uuid.UUID,
    payload: SendPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _campaign(db, principal, campaign_id)
    if campaign.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "This campaign was already sent or scheduled.")
    if not campaign.subject.strip() or not campaign.body.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add a subject and a message before sending.")
    count = await campaign_service.prepare_recipients(db, principal, campaign)
    if count == 0:
        await db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nobody in this audience can receive email (no address or unsubscribed).")
    scheduled = payload.scheduled_at
    if scheduled is not None and scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    if scheduled and scheduled > utcnow() + timedelta(minutes=1):
        campaign.status = "scheduled"
        campaign.scheduled_at = scheduled
    else:
        campaign.status = "sending"
        campaign.started_at = utcnow()
    record_audit(db, organization_id=principal.organization_id, action="send", entity_type="campaign", entity_id=campaign.id, entity_label=campaign.name, changes={"recipients": count})
    await db.commit()
    if campaign.status == "sending":
        await campaign_service.deliver(db, campaign_id=campaign.id, limit=campaign_service.INLINE_SEND_LIMIT)
        await db.refresh(campaign)
    return await campaign_out(db, campaign, detail=True)


class TestPayload(BaseModel):
    email: str | None = None
    subject: str | None = None
    body: str | None = None


@router.post("/campaigns/{campaign_id}/test", dependencies=[Depends(require_feature("mass_email"))])
async def send_test(
    campaign_id: uuid.UUID,
    payload: TestPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    campaign = await _campaign(db, principal, campaign_id)
    to = (payload.email or principal.email).strip()
    user = await db.get(User, principal.user_id)
    context = {
        "first_name": user.full_name.split(" ")[0],
        "last_name": " ".join(user.full_name.split(" ")[1:]),
        "full_name": user.full_name,
        "email": to,
        "sender_name": user.full_name,
        "organization_name": (await db.get(Organization, principal.organization_id)).name,
        "cf": {},
    }
    subject = render(payload.subject or campaign.subject, context)
    body = render(payload.body or campaign.body, context)
    config = await mailer.sender_config(db, principal.organization_id, principal.user_id)
    try:
        delivery = await mailer.send(config, to=to, subject=f"[Test] {subject}", text=body, html_body=text_to_html(body))
    except mailer.MailError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"The mail server refused the test: {exc}") from exc
    return {
        "delivery": delivery,
        "message": f"Test sent to {to}." if delivery == "smtp" else "No mail server is connected, so the test was not delivered. Connect Gmail or Outlook in Settings → Integrations.",
    }


@router.get("/campaigns/{campaign_id}/recipients", dependencies=[Depends(require_feature("mass_email"))])
async def campaign_recipients(
    campaign_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await _campaign(db, principal, campaign_id)
    query = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    if status_filter == "opened":
        query = query.where(CampaignRecipient.opened_at.is_not(None))
    elif status_filter == "unsubscribed":
        query = query.where(CampaignRecipient.unsubscribed_at.is_not(None))
    elif status_filter:
        query = query.where(CampaignRecipient.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.order_by(CampaignRecipient.email).offset((page - 1) * 50).limit(50))).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": str(r.id),
                "email": r.email,
                "name": r.name,
                "record_type": r.record_type,
                "record_id": str(r.record_id),
                "status": r.status,
                "delivery": r.delivery,
                "error": r.error,
                "sent_at": r.sent_at,
                "opened_at": r.opened_at,
                "open_count": r.open_count,
                "unsubscribed_at": r.unsubscribed_at,
                "subject": r.subject,
                "body": r.body,
            }
            for r in rows
        ],
    }


# ===========================================================================
# Web forms
# ===========================================================================


class FormPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target: str = Field(default="lead", pattern="^(lead|case)$")
    fields: list[dict[str, Any]] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


def form_out(form: WebForm) -> dict:
    base = get_settings().app_base_url.rstrip("/")
    url = f"{base}/f/{form.slug}"
    return {
        "id": str(form.id),
        "name": form.name,
        "slug": form.slug,
        "target": form.target,
        "fields": form.fields,
        "settings": form.settings,
        "is_active": form.is_active,
        "submission_count": form.submission_count,
        "last_submission_at": form.last_submission_at,
        "url": url,
        "embed_code": f'<iframe src="{url}?embed=1" title="{form.name}" style="width:100%;min-height:640px;border:0" loading="lazy"></iframe>',
        "updated_at": form.updated_at,
    }


SAFE_SETTINGS = ("title", "description", "submit_label", "success_message", "redirect_url", "owner_user_id", "notify_user_id", "lead_source", "accent")


def _clean_settings(settings: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: settings.get(key) for key in SAFE_SETTINGS if settings.get(key) not in (None, "")}
    for key in ("owner_user_id", "notify_user_id"):
        if key in cleaned:
            try:
                cleaned[key] = str(uuid.UUID(str(cleaned[key])))
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a workspace member.") from exc
    if cleaned.get("redirect_url") and not str(cleaned["redirect_url"]).startswith(("https://", "http://")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The redirect must be a full web address (https://…).")
    return cleaned


@router.get("/forms/options")
async def form_options(principal: Principal = Depends(get_current_principal)):
    return {
        "input_types": list(INPUT_TYPES),
        "lead_fields": [{"key": k, "label": v} for k, v in LEAD_FIELDS.items()],
        "case_fields": [{"key": k, "label": v} for k, v in CASE_FIELDS.items()],
        "defaults": {"lead": default_fields("lead"), "case": default_fields("case")},
        "can_web_to_case": principal.has("web_to_case"),
        "can_use_steps": principal.has("sequential_forms"),
    }


@router.get("/forms", dependencies=[Depends(require_feature("web_forms"))])
async def list_forms(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    forms = (
        await db.execute(select(WebForm).where(WebForm.organization_id == principal.organization_id).order_by(WebForm.created_at.desc()))
    ).scalars().all()
    return [form_out(f) for f in forms]


@router.post("/forms", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("web_forms"))])
async def create_form(
    payload: FormPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    if payload.target == "case" and not principal.has("web_to_case"):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Web-to-case forms are part of the Pro plan.")
    await enforce_limit(db, principal.organization_id, plan_for(principal.plan), "web_forms", WebForm)
    fields = validate_fields(payload.target, payload.fields or default_fields(payload.target), allow_steps=principal.has("sequential_forms"))
    form = WebForm(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        name=payload.name.strip(),
        slug=new_slug(),
        target=payload.target,
        fields=fields,
        settings=_clean_settings(payload.settings),
        is_active=payload.is_active,
        created_by_user_id=principal.user_id,
    )
    db.add(form)
    await db.commit()
    return form_out(form)


async def _form(db: AsyncSession, principal: Principal, form_id: uuid.UUID) -> WebForm:
    form = await db.get(WebForm, form_id)
    if form is None or form.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found.")
    return form


@router.get("/forms/{form_id}", dependencies=[Depends(require_feature("web_forms"))])
async def get_form(
    form_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return form_out(await _form(db, principal, form_id))


@router.patch("/forms/{form_id}", dependencies=[Depends(require_feature("web_forms"))])
async def update_form(
    form_id: uuid.UUID,
    payload: FormPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    form = await _form(db, principal, form_id)
    if payload.target != form.target:
        raise HTTPException(status.HTTP_409_CONFLICT, "A form's target can't change. Create a new form instead.")
    form.name = payload.name.strip()
    if payload.fields is not None:
        form.fields = validate_fields(form.target, payload.fields, allow_steps=principal.has("sequential_forms"))
    form.settings = _clean_settings(payload.settings)
    form.is_active = payload.is_active
    await db.commit()
    return form_out(form)


@router.delete("/forms/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    form = await _form(db, principal, form_id)
    await db.delete(form)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/forms/{form_id}/submissions", dependencies=[Depends(require_feature("web_forms"))])
async def form_submissions(
    form_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    await _form(db, principal, form_id)
    rows = (
        await db.execute(
            select(WebFormSubmission).where(WebFormSubmission.form_id == form_id).order_by(WebFormSubmission.created_at.desc()).limit(100)
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "data": r.data,
            "attribution": r.attribution,
            "record_type": r.record_type,
            "record_id": str(r.record_id) if r.record_id else None,
            "link": f"/{'leads' if r.record_type == 'lead' else 'cases'}/{r.record_id}" if r.record_id else None,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ===========================================================================
# Customer journeys
# ===========================================================================


class JourneyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    object_type: str = Field(default="lead", pattern="^(lead|contact)$")
    trigger: str = Field(default="created", pattern="^(created|manual)$")
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)


async def journey_out(db: AsyncSession, journey: Journey) -> dict:
    counts = dict(
        (
            await db.execute(
                select(JourneyEnrollment.status, func.count()).where(JourneyEnrollment.journey_id == journey.id).group_by(JourneyEnrollment.status)
            )
        ).all()
    )
    return {
        "id": str(journey.id),
        "name": journey.name,
        "description": journey.description,
        "object_type": journey.object_type,
        "trigger": journey.trigger,
        "conditions": journey.conditions,
        "steps": journey.steps,
        "status": journey.status,
        "enrollments": {
            "active": counts.get("active", 0),
            "completed": counts.get("completed", 0),
            "exited": counts.get("exited", 0),
            "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
        },
        "updated_at": journey.updated_at,
    }


@router.get("/journeys", dependencies=[Depends(require_feature("customer_journeys"))])
async def list_journeys(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    journeys = (
        await db.execute(select(Journey).where(Journey.organization_id == principal.organization_id).order_by(Journey.created_at.desc()))
    ).scalars().all()
    return [await journey_out(db, j) for j in journeys]


@router.post("/journeys", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("customer_journeys"))])
async def create_journey(
    payload: JourneyPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey_service.validate_steps(payload.object_type, payload.steps, payload.conditions)
    journey = Journey(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        status="draft",
        **payload.model_dump(),
    )
    db.add(journey)
    await db.commit()
    return await journey_out(db, journey)


async def _journey(db: AsyncSession, principal: Principal, journey_id: uuid.UUID) -> Journey:
    journey = await db.get(Journey, journey_id)
    if journey is None or journey.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Journey not found.")
    return journey


@router.get("/journeys/{journey_id}", dependencies=[Depends(require_feature("customer_journeys"))])
async def get_journey(
    journey_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    data = await journey_out(db, journey)
    enrollments = (
        await db.execute(
            select(JourneyEnrollment).where(JourneyEnrollment.journey_id == journey.id).order_by(JourneyEnrollment.updated_at.desc()).limit(100)
        )
    ).scalars().all()
    data["recent"] = [
        {
            "id": str(e.id),
            "record_type": e.record_type,
            "record_id": str(e.record_id),
            "record_label": e.record_label,
            "status": e.status,
            "current_step": e.current_step,
            "next_run_at": e.next_run_at,
            "history": (e.history or [])[-5:],
            "updated_at": e.updated_at,
        }
        for e in enrollments
    ]
    return data


@router.put("/journeys/{journey_id}", dependencies=[Depends(require_feature("customer_journeys"))])
async def update_journey(
    journey_id: uuid.UUID,
    payload: JourneyPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    journey_service.validate_steps(payload.object_type, payload.steps, payload.conditions)
    if payload.object_type != journey.object_type:
        active = (
            await db.execute(select(func.count()).select_from(JourneyEnrollment).where(JourneyEnrollment.journey_id == journey.id))
        ).scalar_one()
        if active:
            raise HTTPException(status.HTTP_409_CONFLICT, "People are already enrolled, so the record type can't change.")
    for key, value in payload.model_dump().items():
        setattr(journey, key, value)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return await journey_out(db, journey)


class JourneyStatus(BaseModel):
    status: str = Field(pattern="^(active|paused|draft)$")


@router.post("/journeys/{journey_id}/status", dependencies=[Depends(require_feature("customer_journeys"))])
async def set_journey_status(
    journey_id: uuid.UUID,
    payload: JourneyStatus,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    journey.status = payload.status
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return await journey_out(db, journey)


class EnrollPayload(BaseModel):
    record_ids: list[uuid.UUID] = Field(default_factory=list)
    all_matching: bool = False


@router.post("/journeys/{journey_id}/enroll", dependencies=[Depends(require_feature("customer_journeys"))])
async def enroll_records(
    journey_id: uuid.UUID,
    payload: EnrollPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    model = Lead if journey.object_type == "lead" else Contact
    if payload.all_matching:
        from app.services.reporting import ReportFilter, _Query, build_condition
        from app.services.catalog import OBJECTS

        q = _Query(OBJECTS[journey.object_type])
        conditions = [build_condition(q, ReportFilter(**c), principal) for c in journey.conditions or []]
        records = (
            await db.execute(q.apply_joins(select(model)).where(model.organization_id == principal.organization_id, *conditions).limit(5000))
        ).scalars().all()
    else:
        records = (
            await db.execute(select(model).where(model.organization_id == principal.organization_id, model.id.in_(payload.record_ids)))
        ).scalars().all()
    added = await journey_service.enroll(db, journey, list(records))
    await db.commit()
    return {"enrolled": added, "journey": await journey_out(db, journey)}


@router.post("/journeys/{journey_id}/run", dependencies=[Depends(require_feature("customer_journeys"))])
async def run_journey_now(
    journey_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    if journey.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Turn the journey on first.")
    due = (
        await db.execute(
            select(JourneyEnrollment).where(
                JourneyEnrollment.journey_id == journey.id,
                JourneyEnrollment.status == "active",
                JourneyEnrollment.next_run_at <= utcnow(),
            ).limit(200)
        )
    ).scalars().all()
    for enrollment in due:
        await journey_service.advance(db, enrollment)
    await db.commit()
    sent = await campaign_service.deliver(db, limit=200)
    return {"processed": len(due), "emails_processed": sent, "journey": await journey_out(db, journey)}


@router.delete("/journeys/{journey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_journey(
    journey_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    journey = await _journey(db, principal, journey_id)
    await db.delete(journey)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Account based marketing
# ===========================================================================

TIERS = ("tier_1", "tier_2", "tier_3")
COMMITTEE = (
    ("Decision maker", ("owner", "ceo", "president", "chief", "founder", "vp", "vice president", "managing", "partner", "principal", "general manager")),
    ("Champion", ("director", "head of", "manager", "lead")),
    ("Finance", ("cfo", "finance", "controller", "accounting", "treasurer")),
    ("Technical", ("cto", "cio", "it ", "engineer", "technical", "technology", "security")),
    ("Operations", ("operations", "coo", "office", "fleet", "facilities", "hr", "people")),
)


def committee_role(title: str | None) -> str | None:
    if not title:
        return None
    lowered = f" {title.lower()} "
    for role, words in COMMITTEE:
        if any(word in lowered for word in words):
            return role
    return None


class TargetPayload(BaseModel):
    account_ids: list[uuid.UUID] = Field(min_length=1)
    tier: str = Field(default="tier_2", pattern="^(tier_1|tier_2|tier_3)$")
    goal: str | None = Field(default=None, max_length=300)


class TargetUpdate(BaseModel):
    tier: str = Field(pattern="^(tier_1|tier_2|tier_3)$")
    goal: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)
    owner_user_id: uuid.UUID | None = None


async def _abm_rows(db: AsyncSession, principal: Principal) -> list[dict]:
    org = principal.organization_id
    targets = (await db.execute(select(TargetAccount).where(TargetAccount.organization_id == org))).scalars().all()
    if not targets:
        return []
    account_ids = [t.account_id for t in targets]
    accounts = {a.id: a for a in (await db.execute(select(Account).where(Account.id.in_(account_ids)))).scalars()}
    now = utcnow()
    act30 = dict(
        (
            await db.execute(
                select(Activity.account_id, func.count())
                .where(Activity.organization_id == org, Activity.account_id.in_(account_ids), Activity.occurred_at >= now - timedelta(days=30))
                .group_by(Activity.account_id)
            )
        ).all()
    )
    act90 = dict(
        (
            await db.execute(
                select(Activity.account_id, func.count())
                .where(Activity.organization_id == org, Activity.account_id.in_(account_ids), Activity.occurred_at >= now - timedelta(days=90))
                .group_by(Activity.account_id)
            )
        ).all()
    )
    last = dict(
        (
            await db.execute(
                select(Activity.account_id, func.max(Activity.occurred_at))
                .where(Activity.organization_id == org, Activity.account_id.in_(account_ids))
                .group_by(Activity.account_id)
            )
        ).all()
    )
    contacts = (
        await db.execute(select(Contact).where(Contact.organization_id == org, Contact.account_id.in_(account_ids), Contact.is_archived.is_(False)))
    ).scalars().all()
    engaged_contact_ids = set(
        (
            await db.execute(
                select(Activity.contact_id).where(
                    Activity.organization_id == org,
                    Activity.contact_id.in_([c.id for c in contacts] or [uuid.uuid4()]),
                    Activity.occurred_at >= now - timedelta(days=90),
                )
            )
        ).scalars()
    )
    opens = dict(
        (
            await db.execute(
                select(Contact.account_id, func.count())
                .join(CampaignRecipient, (CampaignRecipient.record_id == Contact.id) & (CampaignRecipient.record_type == "contact"))
                .where(Contact.account_id.in_(account_ids), CampaignRecipient.opened_at >= now - timedelta(days=90))
                .group_by(Contact.account_id)
            )
        ).all()
    )
    open_stage_ids = select(PipelineStage.id).where(PipelineStage.organization_id == org, PipelineStage.category == "open")
    won_stage_ids = select(PipelineStage.id).where(PipelineStage.organization_id == org, PipelineStage.category == "won")
    pipeline = dict(
        (
            await db.execute(
                select(Opportunity.account_id, func.coalesce(func.sum(Opportunity.amount), 0))
                .where(Opportunity.organization_id == org, Opportunity.account_id.in_(account_ids), Opportunity.stage_id.in_(open_stage_ids))
                .group_by(Opportunity.account_id)
            )
        ).all()
    )
    won = dict(
        (
            await db.execute(
                select(Opportunity.account_id, func.coalesce(func.sum(Opportunity.amount), 0))
                .where(Opportunity.organization_id == org, Opportunity.account_id.in_(account_ids), Opportunity.stage_id.in_(won_stage_ids))
                .group_by(Opportunity.account_id)
            )
        ).all()
    )
    owners = await names_for(db, org, "user", [t.owner_user_id for t in targets] + [a.owner_user_id for a in accounts.values()])
    rows = []
    for target in targets:
        account = accounts.get(target.account_id)
        if account is None:
            continue
        people = [c for c in contacts if c.account_id == account.id]
        roles = sorted({role for role in (committee_role(c.job_title) for c in people) if role})
        engaged = len([c for c in people if c.id in engaged_contact_ids])
        score = min(
            100,
            4 * act30.get(account.id, 0)
            + act90.get(account.id, 0)
            + 10 * engaged
            + 3 * opens.get(account.id, 0)
            + (15 if float(pipeline.get(account.id, 0)) > 0 else 0)
            + 4 * len(roles),
        )
        rows.append(
            {
                "id": str(target.id),
                "account_id": str(account.id),
                "account_name": account.name,
                "industry": account.industry,
                "tier": target.tier,
                "goal": target.goal,
                "notes": target.notes,
                "owner_user_id": str(target.owner_user_id or account.owner_user_id) if (target.owner_user_id or account.owner_user_id) else None,
                "owner_name": owners.get(str(target.owner_user_id or account.owner_user_id)),
                "engagement_score": score,
                "engagement": "hot" if score >= 60 else "warm" if score >= 25 else "cold",
                "activities_30d": act30.get(account.id, 0),
                "activities_90d": act90.get(account.id, 0),
                "last_activity_at": last.get(account.id),
                "contacts": len(people),
                "engaged_contacts": engaged,
                "email_opens_90d": opens.get(account.id, 0),
                "open_pipeline": float(pipeline.get(account.id, 0)),
                "won_revenue": float(won.get(account.id, 0)),
                "committee_covered": roles,
                "committee_missing": [role for role, _ in COMMITTEE if role not in roles],
            }
        )
    rows.sort(key=lambda r: (TIERS.index(r["tier"]), -r["engagement_score"]))
    return rows


@router.get("/abm/accounts", dependencies=[Depends(require_feature("abm"))])
async def abm_accounts(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    rows = await _abm_rows(db, principal)
    return {
        "items": rows,
        "summary": {
            "targets": len(rows),
            "by_tier": {tier: len([r for r in rows if r["tier"] == tier]) for tier in TIERS},
            "hot": len([r for r in rows if r["engagement"] == "hot"]),
            "open_pipeline": sum(r["open_pipeline"] for r in rows),
            "won_revenue": sum(r["won_revenue"] for r in rows),
            "average_engagement": round(sum(r["engagement_score"] for r in rows) / len(rows)) if rows else 0,
        },
    }


@router.post("/abm/accounts", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("abm"))])
async def add_targets(
    payload: TargetPayload,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    existing = set(
        (await db.execute(select(TargetAccount.account_id).where(TargetAccount.organization_id == principal.organization_id))).scalars()
    )
    accounts = (
        await db.execute(select(Account).where(Account.organization_id == principal.organization_id, Account.id.in_(payload.account_ids)))
    ).scalars().all()
    added = 0
    for account in accounts:
        if account.id in existing:
            continue
        db.add(
            TargetAccount(
                id=uuid.uuid4(),
                organization_id=principal.organization_id,
                account_id=account.id,
                tier=payload.tier,
                goal=payload.goal,
                owner_user_id=account.owner_user_id,
            )
        )
        added += 1
    await db.commit()
    return {"added": added}


async def _target(db: AsyncSession, principal: Principal, target_id: uuid.UUID) -> TargetAccount:
    target = await db.get(TargetAccount, target_id)
    if target is None or target.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target account not found.")
    return target


@router.patch("/abm/accounts/{target_id}", dependencies=[Depends(require_feature("abm"))])
async def update_target(
    target_id: uuid.UUID,
    payload: TargetUpdate,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    target = await _target(db, principal, target_id)
    target.tier = payload.tier
    target.goal = payload.goal
    target.notes = payload.notes
    target.owner_user_id = payload.owner_user_id
    await db.commit()
    return {"message": "Saved."}


@router.delete("/abm/accounts/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_target(
    target_id: uuid.UUID,
    principal: Principal = Depends(require_permission("marketing.manage")),
    db: AsyncSession = Depends(get_db),
):
    target = await _target(db, principal, target_id)
    await db.delete(target)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Google Ads
# ===========================================================================


class GoogleAdsSettings(BaseModel):
    lead_conversion_name: str = Field(default="AIVA lead", min_length=1, max_length=100)
    won_conversion_name: str = Field(default="AIVA closed deal", min_length=1, max_length=100)
    timezone: str = Field(default="America/Chicago", max_length=60)


async def _google(db: AsyncSession, principal: Principal) -> Integration | None:
    return (
        await db.execute(
            select(Integration).where(Integration.organization_id == principal.organization_id, Integration.kind == "google_ads")
        )
    ).scalar_one_or_none()


async def _google_conversions(db: AsyncSession, principal: Principal) -> list[dict]:
    leads = (
        await db.execute(
            select(Lead).where(
                Lead.organization_id == principal.organization_id,
                Lead.custom_fields["_attribution"]["gclid"].astext.is_not(None),
            ).order_by(Lead.created_at.desc()).limit(2000)
        )
    ).scalars().all()
    won_stage_ids = set(
        (
            await db.execute(
                select(PipelineStage.id).where(PipelineStage.organization_id == principal.organization_id, PipelineStage.category == "won")
            )
        ).scalars()
    )
    opp_ids = [lead.converted_opportunity_id for lead in leads if lead.converted_opportunity_id]
    opps = {o.id: o for o in (await db.execute(select(Opportunity).where(Opportunity.id.in_(opp_ids or [uuid.uuid4()])))).scalars()}
    rows = []
    for lead in leads:
        attribution = (lead.custom_fields or {}).get("_attribution") or {}
        rows.append(
            {
                "kind": "lead",
                "gclid": attribution.get("gclid"),
                "name": f"{lead.first_name} {lead.last_name}".strip(),
                "at": lead.created_at,
                "value": None,
                "lead_id": str(lead.id),
                "campaign": attribution.get("utm_campaign"),
            }
        )
        opp = opps.get(lead.converted_opportunity_id)
        if opp is not None and opp.stage_id in won_stage_ids:
            rows.append(
                {
                    "kind": "won",
                    "gclid": attribution.get("gclid"),
                    "name": opp.name,
                    "at": opp.closed_at or opp.updated_at,
                    "value": float(opp.amount or 0),
                    "lead_id": str(lead.id),
                    "campaign": attribution.get("utm_campaign"),
                }
            )
    return rows


@router.get("/google-ads", dependencies=[Depends(require_feature("google_ads"))])
async def google_ads(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _google(db, principal)
    config = (integration.config if integration else {}) or {}
    rows = await _google_conversions(db, principal)
    base = get_settings().app_base_url.rstrip("/")
    organization = await db.get(Organization, principal.organization_id)
    return {
        "connected": integration is not None,
        "settings": {
            "lead_conversion_name": config.get("lead_conversion_name", "AIVA lead"),
            "won_conversion_name": config.get("won_conversion_name", "AIVA closed deal"),
            "timezone": config.get("timezone", "America/Chicago"),
        },
        "webhook_url": f"{base}/api/aiva/public/google-ads/{organization.slug}",
        "has_key": bool(config.get("webhook_key")),
        "stats": {
            "leads": len([r for r in rows if r["kind"] == "lead"]),
            "won": len([r for r in rows if r["kind"] == "won"]),
            "won_value": sum(r["value"] or 0 for r in rows if r["kind"] == "won"),
        },
        "conversions": rows[:100],
    }


@router.put("/google-ads", dependencies=[Depends(require_feature("google_ads"))])
async def save_google_ads(
    payload: GoogleAdsSettings,
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _google(db, principal)
    if integration is None:
        integration = Integration(
            id=uuid.uuid4(), organization_id=principal.organization_id, kind="google_ads", config={}, events=[], connected_by_user_id=principal.user_id
        )
        db.add(integration)
    integration.config = {**(integration.config or {}), **payload.model_dump()}
    await db.commit()
    return await google_ads(principal, db)


@router.post("/google-ads/key", dependencies=[Depends(require_feature("google_ads"))])
async def rotate_google_key(
    principal: Principal = Depends(require_permission("admin.settings")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _google(db, principal)
    if integration is None:
        integration = Integration(
            id=uuid.uuid4(), organization_id=principal.organization_id, kind="google_ads", config={}, events=[], connected_by_user_id=principal.user_id
        )
        db.add(integration)
    key = new_token(nbytes=18)
    integration.config = {**(integration.config or {}), "webhook_key": encrypt(key)}
    await db.commit()
    return {"key": key}


@router.get("/google-ads/conversions.csv", dependencies=[Depends(require_feature("google_ads"))])
async def google_conversions_csv(
    principal: Principal = Depends(require_permission("records.export")),
    db: AsyncSession = Depends(get_db),
):
    integration = await _google(db, principal)
    config = (integration.config if integration else {}) or {}
    rows = await _google_conversions(db, principal)
    buffer = io.StringIO()
    buffer.write(f"Parameters:TimeZone={config.get('timezone', 'America/Chicago')}\n")
    writer = csv.writer(buffer)
    writer.writerow(["Google Click ID", "Conversion Name", "Conversion Time", "Conversion Value", "Conversion Currency"])
    for row in rows:
        name = config.get("lead_conversion_name", "AIVA lead") if row["kind"] == "lead" else config.get("won_conversion_name", "AIVA closed deal")
        moment = row["at"].strftime("%Y-%m-%d %H:%M:%S") if row["at"] else ""
        writer.writerow([row["gclid"], name, moment, f"{row['value']:.2f}" if row["value"] is not None else "", "USD" if row["value"] is not None else ""])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="google-ads-offline-conversions.csv"'},
    )
