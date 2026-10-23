"""Sales power tools: products, quotes, lead scoring and forecasting."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal, require_feature, require_permission
from app.db.session import get_db
from app.models.account import Account
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.membership import OrganizationMembership
from app.models.opportunity import Opportunity
from app.models.organization import Organization
from app.models.pipeline_stage import PipelineStage
from app.models.platform import ForecastQuota, LeadScoringRule, Product, Quote, QuoteLineItem
from app.models.user import User
from app.services.automation import (
    OPERATORS,
    invalidate_automation_cache,
    record_audit,
    score_lead,
)
from app.services.catalog import OBJECTS
from app.services.lookups import names_for

router = APIRouter(tags=["Sales"])

CENT = Decimal("0.01")


def money(value: Decimal | float | int | str | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


# ===========================================================================
# Products
# ===========================================================================


class ProductPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=80)
    description: str | None = None
    family: str | None = Field(default=None, max_length=80)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_frequency: str = Field(default="one_time", pattern="^(one_time|monthly|quarterly|annual)$")
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    is_active: bool = True


def product_out(product: Product) -> dict:
    return {
        "id": str(product.id),
        "name": product.name,
        "sku": product.sku,
        "description": product.description,
        "family": product.family,
        "unit_price": float(product.unit_price),
        "currency": product.currency,
        "billing_frequency": product.billing_frequency,
        "tax_rate": float(product.tax_rate),
        "is_active": product.is_active,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


@router.get("/products", dependencies=[Depends(require_feature("cpq"))])
async def list_products(
    search: str | None = Query(default=None, max_length=200),
    family: str | None = None,
    active: bool | None = None,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Product).where(Product.organization_id == principal.organization_id)
    if search:
        query = query.where(or_(Product.name.ilike(f"%{search}%"), Product.sku.ilike(f"%{search}%")))
    if family:
        query = query.where(Product.family == family)
    if active is not None:
        query = query.where(Product.is_active.is_(active))
    products = (await db.execute(query.order_by(Product.family.nulls_last(), Product.name))).scalars().all()
    return [product_out(p) for p in products]


async def _check_sku(db: AsyncSession, organization_id: uuid.UUID, sku: str | None, exclude: uuid.UUID | None = None):
    if not sku:
        return
    query = select(Product.id).where(Product.organization_id == organization_id, func.lower(Product.sku) == sku.lower())
    if exclude:
        query = query.where(Product.id != exclude)
    if (await db.execute(query)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Another product already uses SKU {sku}.")


@router.post("/products", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("cpq"))])
async def create_product(
    payload: ProductPayload,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    data["sku"] = (data["sku"] or "").strip() or None
    await _check_sku(db, principal.organization_id, data["sku"])
    product = Product(id=uuid.uuid4(), organization_id=principal.organization_id, **data)
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product_out(product)


async def _get_product(db: AsyncSession, principal: Principal, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None or product.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


@router.patch("/products/{product_id}", dependencies=[Depends(require_feature("cpq"))])
async def update_product(
    product_id: uuid.UUID,
    payload: ProductPayload,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    product = await _get_product(db, principal, product_id)
    data = payload.model_dump()
    data["sku"] = (data["sku"] or "").strip() or None
    await _check_sku(db, principal.organization_id, data["sku"], exclude=product.id)
    for key, value in data.items():
        setattr(product, key, value)
    await db.commit()
    await db.refresh(product)
    return product_out(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("cpq"))])
async def delete_product(
    product_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.products")),
    db: AsyncSession = Depends(get_db),
):
    product = await _get_product(db, principal, product_id)
    await db.delete(product)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Quotes
# ===========================================================================


class LineItemPayload(BaseModel):
    product_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=12, decimal_places=2)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    tax_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class QuoteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    opportunity_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    valid_until: date | None = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    terms: str | None = None
    notes: str | None = None
    line_items: list[LineItemPayload] = Field(default_factory=list)


class QuoteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    opportunity_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    valid_until: date | None = None
    terms: str | None = None
    notes: str | None = None


class QuoteStatusUpdate(BaseModel):
    status: str = Field(pattern="^(draft|sent|accepted|declined|expired)$")
    sync_opportunity_amount: bool = True


def recalculate(quote: Quote) -> None:
    subtotal = discount_total = tax_total = Decimal("0")
    for item in quote.line_items:
        gross = money(Decimal(item.quantity) * Decimal(item.unit_price))
        discount = money(gross * Decimal(item.discount_percent) / 100)
        net = gross - discount
        tax = money(net * Decimal(item.tax_percent) / 100)
        item.line_total = net
        subtotal += gross
        discount_total += discount
        tax_total += tax
    quote.subtotal = money(subtotal)
    quote.discount_total = money(discount_total)
    quote.tax_total = money(tax_total)
    quote.total = money(subtotal - discount_total + tax_total)


async def _validate_links(db: AsyncSession, organization_id: uuid.UUID, data: dict) -> None:
    for key, model in (("opportunity_id", Opportunity), ("account_id", Account), ("contact_id", Contact)):
        value = data.get(key)
        if value is None:
            continue
        record = await db.get(model, value)
        if record is None or record.organization_id != organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The selected {key.removesuffix('_id')} isn't in this workspace.")
    if data.get("opportunity_id") and not data.get("account_id"):
        opportunity = await db.get(Opportunity, data["opportunity_id"])
        data["account_id"] = opportunity.account_id


def _line_items(quote: Quote, organization_id: uuid.UUID, items: list[LineItemPayload]) -> None:
    quote.line_items.clear()
    for position, item in enumerate(items):
        quote.line_items.append(
            QuoteLineItem(id=uuid.uuid4(), organization_id=organization_id, position=position, **item.model_dump())
        )


async def quote_out(db: AsyncSession, quote: Quote, detail: bool = False) -> dict:
    org = quote.organization_id
    accounts = await names_for(db, org, "account", [quote.account_id])
    opps = await names_for(db, org, "opportunity", [quote.opportunity_id])
    contacts = await names_for(db, org, "contact", [quote.contact_id])
    owners = await names_for(db, org, "user", [quote.owner_user_id])
    data = {
        "id": str(quote.id),
        "number": quote.number,
        "display_number": f"Q-{quote.number}",
        "name": quote.name,
        "status": quote.status,
        "opportunity_id": str(quote.opportunity_id) if quote.opportunity_id else None,
        "opportunity_name": opps.get(str(quote.opportunity_id)),
        "account_id": str(quote.account_id) if quote.account_id else None,
        "account_name": accounts.get(str(quote.account_id)),
        "contact_id": str(quote.contact_id) if quote.contact_id else None,
        "contact_name": contacts.get(str(quote.contact_id)),
        "owner_user_id": str(quote.owner_user_id) if quote.owner_user_id else None,
        "owner_name": owners.get(str(quote.owner_user_id)),
        "valid_until": quote.valid_until,
        "price_book_id": str(quote.price_book_id) if quote.price_book_id else None,
        "currency": quote.currency,
        "subtotal": float(quote.subtotal),
        "discount_total": float(quote.discount_total),
        "tax_total": float(quote.tax_total),
        "total": float(quote.total),
        "line_count": len(quote.line_items),
        "sent_at": quote.sent_at,
        "accepted_at": quote.accepted_at,
        "declined_at": quote.declined_at,
        "created_at": quote.created_at,
        "updated_at": quote.updated_at,
    }
    if detail:
        data["terms"] = quote.terms
        data["notes"] = quote.notes
        data["line_items"] = [
            {
                "id": str(item.id),
                "product_id": str(item.product_id) if item.product_id else None,
                "name": item.name,
                "description": item.description,
                "quantity": float(item.quantity),
                "unit_price": float(item.unit_price),
                "discount_percent": float(item.discount_percent),
                "tax_percent": float(item.tax_percent),
                "line_total": float(item.line_total),
            }
            for item in quote.line_items
        ]
    return data


async def _get_quote(db: AsyncSession, principal: Principal, quote_id: uuid.UUID) -> Quote:
    quote = await db.get(Quote, quote_id)
    if quote is None or quote.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found.")
    return quote


@router.get("/quotes", dependencies=[Depends(require_feature("cpq"))])
async def list_quotes(
    quote_status: str | None = Query(default=None, alias="status"),
    account_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    query = select(Quote).where(Quote.organization_id == principal.organization_id)
    if quote_status:
        query = query.where(Quote.status == quote_status)
    if account_id:
        query = query.where(Quote.account_id == account_id)
    if opportunity_id:
        query = query.where(Quote.opportunity_id == opportunity_id)
    if search:
        term = search.strip().lower().removeprefix("q-")
        conditions = [Quote.name.ilike(f"%{search}%")]
        if term.isdigit():
            conditions.append(Quote.number == int(term))
        query = query.where(or_(*conditions))
    quotes = (await db.execute(query.order_by(Quote.number.desc()).limit(500))).scalars().all()

    # Mark quotes past their validity date as expired when read.
    today = date.today()
    changed = False
    for quote in quotes:
        if quote.status in {"draft", "sent"} and quote.valid_until and quote.valid_until < today:
            quote.status = "expired"
            changed = True
    if changed:
        await db.commit()

    return [await quote_out(db, quote) for quote in quotes]


@router.post("/quotes", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("cpq"))])
async def create_quote(
    payload: QuoteCreate,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"line_items"})
    await _validate_links(db, principal.organization_id, data)
    number = (
        await db.execute(select(func.coalesce(func.max(Quote.number), 1000)).where(Quote.organization_id == principal.organization_id))
    ).scalar_one() + 1
    quote = Quote(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        number=number,
        owner_user_id=principal.user_id,
        status="draft",
        valid_until=data.pop("valid_until") or date.today() + timedelta(days=30),
        **data,
    )
    quote.line_items = []
    _line_items(quote, principal.organization_id, payload.line_items)
    recalculate(quote)
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return await quote_out(db, quote, detail=True)


@router.get("/quotes/{quote_id}", dependencies=[Depends(require_feature("cpq"))])
async def get_quote(
    quote_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    return await quote_out(db, await _get_quote(db, principal, quote_id), detail=True)


def _ensure_editable(quote: Quote) -> None:
    if quote.status in {"accepted", "declined"}:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is {quote.status}. Duplicate it to make changes.")


@router.patch("/quotes/{quote_id}", dependencies=[Depends(require_feature("cpq"))])
async def update_quote(
    quote_id: uuid.UUID,
    payload: QuoteUpdate,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    quote = await _get_quote(db, principal, quote_id)
    _ensure_editable(quote)
    data = payload.model_dump(exclude_unset=True)
    await _validate_links(db, principal.organization_id, data)
    for key, value in data.items():
        setattr(quote, key, value)
    await db.commit()
    await db.refresh(quote)
    return await quote_out(db, quote, detail=True)


@router.put("/quotes/{quote_id}/line-items", dependencies=[Depends(require_feature("cpq"))])
async def replace_line_items(
    quote_id: uuid.UUID,
    items: list[LineItemPayload],
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    quote = await _get_quote(db, principal, quote_id)
    _ensure_editable(quote)
    for item in items:
        if item.product_id:
            product = await db.get(Product, item.product_id)
            if product is None or product.organization_id != principal.organization_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "A selected product isn't in this workspace.")
    _line_items(quote, principal.organization_id, items)
    await db.flush()
    recalculate(quote)
    if quote.status == "expired":
        quote.status = "draft"
    await db.commit()
    await db.refresh(quote)
    return await quote_out(db, quote, detail=True)


@router.post("/quotes/{quote_id}/status", dependencies=[Depends(require_feature("cpq"))])
async def set_quote_status(
    quote_id: uuid.UUID,
    payload: QuoteStatusUpdate,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    quote = await _get_quote(db, principal, quote_id)
    target = payload.status
    if target in {"accepted", "declined"} and not principal.can("sales.quotes_approve"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only users who can approve quotes can mark them accepted or declined.")
    if target == "sent" and not quote.line_items:
        raise HTTPException(status.HTTP_409_CONFLICT, "Add at least one line item before sending.")
    if quote.status in {"accepted", "declined"} and target != quote.status:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This quote is already {quote.status}.")
    if target == "sent" and quote.status == "draft" and principal.has("approvals"):
        from app.services.approvals import matching_quote_rule
        from app.services.automation import AutomationBlocked

        rule = await matching_quote_rule(db, quote)
        if rule is not None:
            raise AutomationBlocked(
                409,
                f"This quote needs approval before it's sent ({rule.name}). Submit it for approval.",
                "approval_required",
                {"rule_id": str(rule.id), "rule_name": rule.name, "requested_change": {"status": "sent"}},
            )

    now = datetime.now(timezone.utc)
    stock_warnings: list[str] = []
    if target == "accepted" and quote.status != "accepted" and principal.has("inventory"):
        from app.api.routes.operations import apply_quote_sale

        stock_warnings = await apply_quote_sale(db, quote, principal.user_id)
    quote.status = target
    if target == "sent":
        quote.sent_at = now
    elif target == "accepted":
        quote.accepted_at = now
        if payload.sync_opportunity_amount and quote.opportunity_id:
            opportunity = await db.get(Opportunity, quote.opportunity_id)
            if opportunity is not None:
                opportunity.amount = quote.total
    elif target == "declined":
        quote.declined_at = now
    await db.commit()
    await db.refresh(quote)
    data = await quote_out(db, quote, detail=True)
    if stock_warnings:
        data["stock_warnings"] = stock_warnings
    return data


@router.post("/quotes/{quote_id}/duplicate", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature("cpq"))])
async def duplicate_quote(
    quote_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    source = await _get_quote(db, principal, quote_id)
    number = (
        await db.execute(select(func.max(Quote.number)).where(Quote.organization_id == principal.organization_id))
    ).scalar_one() + 1
    copy = Quote(
        id=uuid.uuid4(),
        organization_id=principal.organization_id,
        number=number,
        name=f"{source.name} (copy)"[:200],
        status="draft",
        opportunity_id=source.opportunity_id,
        account_id=source.account_id,
        contact_id=source.contact_id,
        owner_user_id=principal.user_id,
        valid_until=date.today() + timedelta(days=30),
        currency=source.currency,
        terms=source.terms,
        notes=source.notes,
    )
    copy.line_items = []
    for item in source.line_items:
        copy.line_items.append(
            QuoteLineItem(
                id=uuid.uuid4(),
                organization_id=principal.organization_id,
                product_id=item.product_id,
                name=item.name,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_percent=item.discount_percent,
                tax_percent=item.tax_percent,
                position=item.position,
            )
        )
    recalculate(copy)
    db.add(copy)
    await db.commit()
    await db.refresh(copy)
    return await quote_out(db, copy, detail=True)


@router.delete("/quotes/{quote_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature("cpq"))])
async def delete_quote(
    quote_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.quotes")),
    db: AsyncSession = Depends(get_db),
):
    quote = await _get_quote(db, principal, quote_id)
    if quote.status == "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Accepted quotes are kept for the record and can't be deleted.")
    await db.delete(quote)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# Lead scoring
# ===========================================================================


class ScoringRulePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    field: str
    operator: str
    value: str | None = Field(default=None, max_length=200)
    points: int = Field(ge=-100, le=100)
    is_active: bool = True


def _validate_scoring(payload: ScoringRulePayload) -> None:
    lead_fields = {f.key for f in OBJECTS["lead"].fields}
    if payload.field not in lead_fields:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a lead field.")
    if payload.operator not in OPERATORS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a valid condition.")
    if payload.operator not in {"is_empty", "is_not_empty"} and not (payload.value or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a value to compare against.")


def scoring_out(rule: LeadScoringRule) -> dict:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "field": rule.field,
        "operator": rule.operator,
        "value": rule.value,
        "points": rule.points,
        "is_active": rule.is_active,
    }


@router.get("/lead-scoring/rules")
async def list_scoring_rules(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    rules = (
        await db.execute(
            select(LeadScoringRule)
            .where(LeadScoringRule.organization_id == principal.organization_id)
            .order_by(LeadScoringRule.points.desc(), LeadScoringRule.name)
        )
    ).scalars()
    return [scoring_out(rule) for rule in rules]


@router.post("/lead-scoring/rules", status_code=status.HTTP_201_CREATED)
async def create_scoring_rule(
    payload: ScoringRulePayload,
    principal: Principal = Depends(require_permission("sales.lead_scoring")),
    db: AsyncSession = Depends(get_db),
):
    _validate_scoring(payload)
    rule = LeadScoringRule(id=uuid.uuid4(), organization_id=principal.organization_id, **payload.model_dump())
    db.add(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return scoring_out(rule)


@router.patch("/lead-scoring/rules/{rule_id}")
async def update_scoring_rule(
    rule_id: uuid.UUID,
    payload: ScoringRulePayload,
    principal: Principal = Depends(require_permission("sales.lead_scoring")),
    db: AsyncSession = Depends(get_db),
):
    _validate_scoring(payload)
    rule = await db.get(LeadScoringRule, rule_id)
    if rule is None or rule.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found.")
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return scoring_out(rule)


@router.delete("/lead-scoring/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scoring_rule(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_permission("sales.lead_scoring")),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.get(LeadScoringRule, rule_id)
    if rule is None or rule.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found.")
    await db.delete(rule)
    await db.commit()
    invalidate_automation_cache(principal.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _active_scoring(db: AsyncSession, organization_id: uuid.UUID) -> list[dict]:
    rules = (
        await db.execute(
            select(LeadScoringRule).where(
                LeadScoringRule.organization_id == organization_id, LeadScoringRule.is_active.is_(True)
            )
        )
    ).scalars()
    return [scoring_out(rule) | {"points": rule.points} for rule in rules]


def grade(score: int) -> str:
    return "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"


@router.post("/lead-scoring/recalculate")
async def recalculate_scores(
    principal: Principal = Depends(require_permission("sales.lead_scoring")),
    db: AsyncSession = Depends(get_db),
):
    rules = await _active_scoring(db, principal.organization_id)
    leads = (await db.execute(select(Lead).where(Lead.organization_id == principal.organization_id))).scalars().all()
    db.sync_session.info["automation_disabled"] = True
    updated = 0
    for lead in leads:
        new_score, _ = score_lead(lead, rules)
        if new_score != lead.score:
            lead.score = new_score
            updated += 1
    record_audit(
        db,
        organization_id=principal.organization_id,
        action="recalculate",
        entity_type="scoring_rule",
        entity_label="Lead scores recalculated",
        changes={"leads": len(leads), "updated": updated},
    )
    await db.commit()
    return {"leads": len(leads), "updated": updated}


@router.get("/lead-scoring/summary")
async def scoring_summary(
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    scores = (
        await db.execute(
            select(Lead.score).where(Lead.organization_id == principal.organization_id, Lead.status != "converted")
        )
    ).scalars().all()
    distribution = {"A": 0, "B": 0, "C": 0, "D": 0}
    for score in scores:
        distribution[grade(score)] += 1
    return {
        "leads": len(scores),
        "average": round(sum(scores) / len(scores), 1) if scores else 0,
        "distribution": distribution,
    }


@router.get("/lead-scoring/leads/{lead_id}")
async def lead_score_breakdown(
    lead_id: uuid.UUID,
    principal: Principal = Depends(require_permission("records.read")),
    db: AsyncSession = Depends(get_db),
):
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.organization_id != principal.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found.")
    score, breakdown = score_lead(lead, await _active_scoring(db, principal.organization_id))
    return {"lead_id": str(lead.id), "score": score, "grade": grade(score), "breakdown": breakdown}


# ===========================================================================
# Forecasting
# ===========================================================================


def _fiscal_start(settings: dict | None) -> int:
    try:
        month = int((settings or {}).get("fiscal_year_start_month", 1))
        return month if 1 <= month <= 12 else 1
    except (TypeError, ValueError):
        return 1


def period_bounds(key: str, start_month: int) -> tuple[date, date]:
    try:
        year_text, quarter_text = key.split("-Q")
        year, quarter = int(year_text), int(quarter_text)
        assert 1 <= quarter <= 4
    except (ValueError, AssertionError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Use a period like 2026-Q4.") from exc
    month_index = (start_month - 1) + (quarter - 1) * 3
    start = date(year + month_index // 12, month_index % 12 + 1, 1)
    end_index = month_index + 3
    end = date(year + end_index // 12, end_index % 12 + 1, 1) - timedelta(days=1)
    return start, end


def period_for(day: date, start_month: int) -> str:
    offset = (day.month - start_month) % 12
    fiscal_year = day.year if day.month >= start_month else day.year - 1
    return f"{fiscal_year}-Q{offset // 3 + 1}"


def shift_period(key: str, quarters: int) -> str:
    year, quarter = key.split("-Q")
    index = int(year) * 4 + int(quarter) - 1 + quarters
    return f"{index // 4}-Q{index % 4 + 1}"


class QuotaPayload(BaseModel):
    user_id: uuid.UUID
    period: str = Field(pattern=r"^\d{4}-Q[1-4]$")
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


@router.get("/forecast")
async def forecast(
    period: str | None = Query(default=None, pattern=r"^\d{4}-Q[1-4]$"),
    team_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_permission("sales.forecast_view")),
    db: AsyncSession = Depends(get_db),
):
    organization = await db.get(Organization, principal.organization_id)
    start_month = _fiscal_start(organization.settings)
    current = period_for(date.today(), start_month)
    period = period or current
    start, end = period_bounds(period, start_month)

    members_query = (
        select(User.id, User.full_name, OrganizationMembership.team_id)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            OrganizationMembership.organization_id == principal.organization_id,
            OrganizationMembership.status == "active",
        )
    )
    if team_id:
        members_query = members_query.where(OrganizationMembership.team_id == team_id)
    if not principal.can("records.view_all"):
        members_query = members_query.where(User.id == principal.user_id)
    members = (await db.execute(members_query)).all()
    member_ids = [member_id for member_id, _, _ in members]

    close_day = func.coalesce(func.date(Opportunity.closed_at), Opportunity.expected_close_date)
    rows = (
        await db.execute(
            select(Opportunity, PipelineStage.category)
            .join(PipelineStage, PipelineStage.id == Opportunity.stage_id)
            .where(
                Opportunity.organization_id == principal.organization_id,
                Opportunity.owner_user_id.in_(member_ids) if member_ids else false(),
                or_(
                    and_(PipelineStage.category == "won", close_day.between(start, end)),
                    and_(PipelineStage.category == "open", Opportunity.expected_close_date.between(start, end)),
                ),
            )
        )
    ).all()

    quotas = dict(
        (
            await db.execute(
                select(ForecastQuota.user_id, ForecastQuota.amount).where(
                    ForecastQuota.organization_id == principal.organization_id,
                    ForecastQuota.period == period,
                )
            )
        ).all()
    )

    def blank(user_id: uuid.UUID, name: str) -> dict:
        return {
            "user_id": str(user_id),
            "name": name,
            "quota": float(quotas.get(user_id, 0) or 0),
            "closed_won": 0.0,
            "commit": 0.0,
            "best_case": 0.0,
            "pipeline": 0.0,
            "omitted": 0.0,
            "open_count": 0,
        }

    by_owner = {member_id: blank(member_id, name) for member_id, name, _ in members}
    deals = []
    for opportunity, category in rows:
        row = by_owner.get(opportunity.owner_user_id)
        if row is None:
            continue
        amount = float(opportunity.amount or 0)
        if category == "won":
            row["closed_won"] += amount
            continue
        bucket = opportunity.forecast_category if opportunity.forecast_category in {"commit", "best_case", "pipeline", "omitted"} else "commit"
        row[bucket] += amount
        row["open_count"] += 1
        deals.append(
            {
                "id": str(opportunity.id),
                "name": opportunity.name,
                "owner_name": row["name"],
                "amount": amount,
                "probability": opportunity.probability,
                "forecast_category": opportunity.forecast_category,
                "expected_close_date": opportunity.expected_close_date,
            }
        )

    owner_rows = sorted(by_owner.values(), key=lambda r: (-(r["closed_won"] + r["commit"]), r["name"]))
    for row in owner_rows:
        row["projected"] = row["closed_won"] + row["commit"]
        row["attainment"] = round(row["closed_won"] / row["quota"] * 100, 1) if row["quota"] else None

    totals = {key: sum(r[key] for r in owner_rows) for key in ("quota", "closed_won", "commit", "best_case", "pipeline", "omitted")}
    totals["projected"] = totals["closed_won"] + totals["commit"]
    totals["gap"] = max(0.0, totals["quota"] - totals["projected"])
    totals["attainment"] = round(totals["closed_won"] / totals["quota"] * 100, 1) if totals["quota"] else None

    return {
        "period": period,
        "current_period": current,
        "start": start,
        "end": end,
        "periods": [shift_period(current, offset) for offset in (-2, -1, 0, 1, 2)],
        "can_manage_quotas": principal.can("sales.forecast_manage"),
        "totals": totals,
        "rows": owner_rows,
        "deals": sorted(deals, key=lambda d: -d["amount"])[:25],
    }


@router.put("/forecast/quotas")
async def set_quota(
    payload: QuotaPayload,
    principal: Principal = Depends(require_permission("sales.forecast_manage")),
    db: AsyncSession = Depends(get_db),
):
    member = (
        await db.execute(
            select(OrganizationMembership.id).where(
                OrganizationMembership.organization_id == principal.organization_id,
                OrganizationMembership.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That user isn't in this workspace.")
    quota = (
        await db.execute(
            select(ForecastQuota).where(
                ForecastQuota.organization_id == principal.organization_id,
                ForecastQuota.user_id == payload.user_id,
                ForecastQuota.period == payload.period,
            )
        )
    ).scalar_one_or_none()
    if quota is None:
        quota = ForecastQuota(id=uuid.uuid4(), organization_id=principal.organization_id, **payload.model_dump())
        db.add(quota)
    else:
        quota.amount = payload.amount
    await db.commit()
    return {"message": "Quota saved.", "amount": float(payload.amount)}
