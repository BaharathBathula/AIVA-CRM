"""Integration endpoints: help desks (Zendesk, Freshdesk, Intercom, Help Scout, Jira Service Management).

* ``/helpdesk/desks``: which desks are on and connected, with what's needed to open tickets.
* ``/helpdesk/records/{type}/{id}``: a contact's, lead's or account's tickets from every desk.
* ``/helpdesk/tickets``: open a ticket for a contact, lead or account.
* ``/helpdesk/cases/{id}``: escalate an AIVA case to a desk, link an existing ticket, unlink.
* ``/helpdesk/{key}/...``: admin tools (sync now, webhook registration and secrets, refresh lists).

Inbound webhooks arrive through the hub (``/public/integrations/{key}/webhook/{token}``)
and are handled in ``app/integrations/providers/helpdesk.py``.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.integrations import core
from app.integrations.core import IntegrationError
from app.integrations.providers import helpdesk
from app.integrations.providers._hd_common import KEYS, PRIORITIES, clean_email, name_of
from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.platform import Case
from app.services.automation import record_audit

router = APIRouter(tags=["Integrations"])
public_router = APIRouter(prefix="/public", tags=["Public"])

FEATURE = [Depends(require_feature(helpdesk.FEATURE))]
RECORD_MODELS = {"contact": Contact, "lead": Lead, "account": Account}
DeskKey = Literal["zendesk", "freshdesk", "intercom", "help_scout", "jira_service_management"]
RecordType = Literal["contact", "lead", "account"]


def _error(exc: IntegrationError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


async def _record(db: AsyncSession, organization_id: uuid.UUID, record_type: str, record_id: uuid.UUID) -> Any:
    model = RECORD_MODELS[record_type]
    # A select (not db.get) so record visibility rules apply.
    found = (await db.execute(select(model).where(model.id == record_id, model.organization_id == organization_id))).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{record_type.title()} not found.")
    return found


async def _case(db: AsyncSession, principal: Principal, case_id: uuid.UUID) -> Case:
    case = await db.get(Case, case_id)
    if case is None or case.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return case


def _no_support(principal: Principal) -> None:
    if principal.support_grant_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Support access can't open tickets for customers.")


async def _account_contacts(db: AsyncSession, account: Account) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Contact)
            .where(Contact.organization_id == account.organization_id, Contact.account_id == account.id, Contact.email.is_not(None), Contact.is_archived.is_(False))
            .order_by(Contact.is_primary.desc(), Contact.last_name)
            .limit(50)
        )
    ).scalars().all()
    return [{"id": str(c.id), "email": clean_email(c.email), "name": f"{c.first_name or ''} {c.last_name or ''}".strip()} for c in rows if clean_email(c.email)]


# ===========================================================================
# Desks and record tickets
# ===========================================================================


@router.get("/helpdesk/desks", dependencies=FEATURE)
async def list_desks(principal: Principal = Depends(require_permission("records.read")), db: AsyncSession = Depends(get_db)):
    return {"desks": await helpdesk.desks(db, principal.organization_id), "can_create": principal.can("records.write")}


@router.get("/helpdesk/records/{record_type}/{record_id}", dependencies=FEATURE)
async def record_tickets(
    record_type: RecordType,
    record_id: uuid.UUID,
    refresh: bool = Query(default=False),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    """Tickets and conversations for a contact, lead or account from every connected desk."""

    org = principal.organization_id
    record = await _record(db, org, record_type, record_id)
    result = await helpdesk.record_tickets(db, org, record_type, record, refresh=refresh)
    out: dict[str, Any] = {
        **result,
        "desks": await helpdesk.desks(db, org),
        "can_create": principal.can("records.write") and principal.support_grant_id is None,
    }
    if record_type == "account":
        out["contacts"] = await _account_contacts(db, record)
    else:
        out["contacts"] = []
    return out


class TicketPayload(BaseModel):
    provider: DeskKey
    record_type: RecordType
    record_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=20000)
    priority: str | None = Field(default=None, max_length=10)
    requester_email: str | None = Field(default=None, max_length=320)
    notify_customer: bool | None = None
    mailbox_id: str | None = Field(default=None, max_length=40)
    request_type_id: str | None = Field(default=None, max_length=40)


@router.post("/helpdesk/tickets", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def create_ticket(payload: TicketPayload, principal: Principal = Depends(require_permission("records.write")), db: AsyncSession = Depends(get_db)):
    """Open a ticket (or conversation) for a contact, lead or account."""

    _no_support(principal)
    org = principal.organization_id
    record = await _record(db, org, payload.record_type, payload.record_id)
    if payload.priority is not None and payload.priority not in PRIORITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Priority is low, normal, high or urgent.")
    name: str | None = None
    if payload.record_type == "account":
        email = clean_email(payload.requester_email)
        contact = None
        if email:
            contact = (
                await db.execute(
                    select(Contact).where(Contact.organization_id == org, Contact.account_id == record.id, func.lower(Contact.email) == email).limit(1)
                )
            ).scalar_one_or_none()
        if contact is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose which of the account's contacts the ticket is for.")
        name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None
    else:
        email = clean_email(record.email)
        if not email:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Add an email address to this {payload.record_type} first; the help desk needs one.")
        name = f"{record.first_name or ''} {record.last_name or ''}".strip() or None
    try:
        row = await helpdesk.create_ticket(
            db, principal, key=payload.provider, record_type=payload.record_type, record=record, subject=payload.subject.strip(), body=payload.body.strip(),
            priority=payload.priority, requester_email=email, requester_name=name, notify_customer=payload.notify_customer,
            options={"mailbox_id": payload.mailbox_id, "request_type_id": payload.request_type_id},
        )
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    record_audit(db, organization_id=org, action="helpdesk_ticket_created", entity_type=payload.record_type, entity_id=record.id,
                 entity_label=f"{name_of(payload.provider)} {(row.data or {}).get('number')}")
    await db.commit()
    from app.integrations.providers._hd_common import ticket_out

    return {"ticket": ticket_out(row)}


# ===========================================================================
# Cases
# ===========================================================================


@router.get("/helpdesk/cases/{case_id}", dependencies=FEATURE)
async def case_tickets(
    case_id: uuid.UUID,
    refresh: bool = Query(default=False),
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    """Tickets this case is escalated to or linked with, with their live status."""

    case = await _case(db, principal, case_id)
    org = principal.organization_id
    contact_email = None
    if case.contact_id:
        contact = await db.get(Contact, case.contact_id)
        contact_email = clean_email(contact.email) if contact is not None else None
    return {
        "items": await helpdesk.case_tickets(db, org, case.id, refresh=refresh),
        "desks": await helpdesk.desks(db, org),
        "can_escalate": principal.support_grant_id is None,
        "contact_email": contact_email or clean_email(((case.custom_fields or {}).get("_requester") or {}).get("email")),
    }


class EscalatePayload(BaseModel):
    provider: DeskKey
    subject: str | None = Field(default=None, max_length=250)
    body: str | None = Field(default=None, max_length=20000)
    priority: str | None = Field(default=None, max_length=10)
    requester_email: str | None = Field(default=None, max_length=320)
    notify_customer: bool | None = None
    mailbox_id: str | None = Field(default=None, max_length=40)
    request_type_id: str | None = Field(default=None, max_length=40)


@router.post("/helpdesk/cases/{case_id}/escalate", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def escalate_case(
    case_id: uuid.UUID,
    payload: EscalatePayload,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    _no_support(principal)
    case = await _case(db, principal, case_id)
    if payload.priority is not None and payload.priority not in PRIORITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Priority is low, normal, high or urgent.")
    try:
        item = await helpdesk.escalate(
            db, principal, case, key=payload.provider, subject=payload.subject, body=payload.body, priority=payload.priority,
            requester_email=payload.requester_email, notify_customer=payload.notify_customer,
            options={"mailbox_id": payload.mailbox_id, "request_type_id": payload.request_type_id},
        )
    except IntegrationError as exc:
        await db.commit()
        raise _error(exc) from exc
    record_audit(db, organization_id=case.organization_id, action="case_escalated", entity_type="case", entity_id=case.id,
                 entity_label=f"CS-{case.number} → {name_of(payload.provider)} {item.get('number')}")
    await db.commit()
    return {"item": item}


class LinkPayload(BaseModel):
    provider: DeskKey
    reference: str = Field(min_length=1, max_length=500)


@router.post("/helpdesk/cases/{case_id}/link", status_code=status.HTTP_201_CREATED, dependencies=FEATURE)
async def link_case(
    case_id: uuid.UUID,
    payload: LinkPayload,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _case(db, principal, case_id)
    try:
        item = await helpdesk.link_existing(db, principal, case, key=payload.provider, reference=payload.reference)
    except IntegrationError as exc:
        await db.rollback()
        raise _error(exc) from exc
    await db.commit()
    return {"item": item}


@router.delete("/helpdesk/cases/{case_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=FEATURE)
async def unlink_case(
    case_id: uuid.UUID,
    link_id: uuid.UUID,
    principal: Principal = Depends(require_permission("service.cases")),
    db: AsyncSession = Depends(get_db),
):
    case = await _case(db, principal, case_id)
    try:
        await helpdesk.unlink(db, principal, case, link_id)
    except IntegrationError as exc:
        raise _error(exc) from exc
    await db.commit()


# ===========================================================================
# Admin tools
# ===========================================================================


async def _admin_connection(db: AsyncSession, principal: Principal, key: str):
    if key not in KEYS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown help desk.")
    try:
        return await core.active_connection(db, principal.organization_id, key)
    except IntegrationError as exc:
        raise _error(exc) from exc


@router.post("/helpdesk/{key}/sync", dependencies=FEATURE)
async def sync_now(key: DeskKey, principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Read recent changes now (a few pages; the rest follows in the background)."""

    connection = await _admin_connection(db, principal, key)
    stored = await helpdesk.sync_connection(db, connection, pages=3)
    core.log(db, principal.organization_id, key, "synced", f"Read {stored} updated {'conversation' if helpdesk.ADAPTERS[key].KIND == 'conversation' else 'ticket'}{'s' if stored != 1 else ''} from {name_of(key)}.",
             connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"stored": stored, "catching_up": bool((connection.config or {}).get("sync_more")), "last_error": connection.last_error if connection.status == "error" else None}


@router.post("/helpdesk/{key}/webhook/register", dependencies=FEATURE)
async def register_webhook(key: Literal["zendesk", "help_scout"], principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)):
    """Create AIVA's webhook in the desk again (after fixing permissions, say)."""

    connection = await _admin_connection(db, principal, key)
    await helpdesk.ADAPTERS[key].setup(db, connection)
    await db.commit()
    return {"webhook": helpdesk.ADAPTERS[key].webhook_info(connection)}


@router.post("/helpdesk/{key}/webhook-secret", dependencies=FEATURE)
async def new_webhook_secret(
    key: Literal["freshdesk", "jira_service_management"], principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)
):
    """A new secret for the desk's webhook. Shown once; paste it into the desk's webhook settings."""

    connection = await _admin_connection(db, principal, key)
    secret = secrets.token_urlsafe(32)
    core.set_secret(connection, "webhook_secret", secret)
    core.log(db, principal.organization_id, key, "webhook_secret_rotated", "A new webhook secret was created. Webhooks without it are now refused.",
             level="warning", connection=connection, user_id=principal.user_id)
    record_audit(db, organization_id=principal.organization_id, action="integration_secret_rotated", entity_type="integration", entity_label=name_of(key))
    await db.commit()
    return {"secret": secret}


@router.delete("/helpdesk/{key}/webhook-secret", dependencies=FEATURE)
async def remove_webhook_secret(
    key: Literal["freshdesk", "jira_service_management"], principal: Principal = Depends(require_permission("admin.settings")), db: AsyncSession = Depends(get_db)
):
    connection = await _admin_connection(db, principal, key)
    core.set_secret(connection, "webhook_secret", None)
    core.log(db, principal.organization_id, key, "webhook_secret_removed", "The webhook secret was removed.", connection=connection, user_id=principal.user_id)
    await db.commit()
    return {"signed": False}


@router.post("/helpdesk/{key}/options/refresh", dependencies=FEATURE)
async def refresh_options(key: Literal["help_scout", "jira_service_management"], principal: Principal = Depends(require_permission("admin.settings")),
                          db: AsyncSession = Depends(get_db)):
    """Re-read Help Scout mailboxes or Jira service desks and request types."""

    connection = await _admin_connection(db, principal, key)
    try:
        if key == "help_scout":
            from app.integrations.providers import _hd_helpscout

            mailboxes = _hd_helpscout._embedded(await _hd_helpscout._call(connection, "GET", "/mailboxes"), "mailboxes")
            found: dict[str, Any] = {"mailboxes": [{"id": str(m.get("id")), "name": m.get("name"), "email": m.get("email")} for m in mailboxes][:50]}
        else:
            from app.integrations.providers import _hd_jira

            found = await _hd_jira.refresh_options(connection)
    except IntegrationError as exc:
        raise _error(exc) from exc
    connection.config = {**(connection.config or {}), **found}
    await db.commit()
    return {"options": helpdesk.ADAPTERS[key].options(connection)}
