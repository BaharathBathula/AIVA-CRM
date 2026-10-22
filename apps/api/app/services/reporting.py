"""Report engine: turns a report definition into SQL and labelled results."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Date, String, and_, cast, false, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import Principal
from app.models.opportunity import Opportunity
from app.models.pipeline_stage import PipelineStage
from app.services.automation import OPERATORS, jsonable
from app.services.catalog import OBJECTS, FieldDef, ObjectDef
from app.services.lookups import names_for

PERIODS = (
    "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
    "this_quarter", "last_quarter", "this_year", "last_year",
    "last_7_days", "last_30_days", "last_90_days", "next_7_days", "next_30_days", "next_90_days",
)
GRANULARITIES = ("day", "week", "month", "quarter", "year")
AGGREGATES = ("count", "sum", "avg", "min", "max")
CHARTS = ("table", "bar", "line", "donut", "metric")
MAX_ROWS = 1000


class ReportFilter(BaseModel):
    field: str
    operator: str = "equals"
    value: Any = None


class ReportSpec(BaseModel):
    object_type: str
    columns: list[str] = Field(default_factory=list)
    filters: list[ReportFilter] = Field(default_factory=list)
    group_by: str | None = None
    date_granularity: str | None = None
    aggregate: str = "count"
    aggregate_field: str | None = None
    sort_field: str | None = None
    sort_direction: str = "desc"
    row_limit: int = Field(default=200, ge=1, le=MAX_ROWS)
    chart_type: str = "table"


def _quarter_start(day: date) -> date:
    return date(day.year, (day.month - 1) // 3 * 3 + 1, 1)


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def period_range(key: str, today: date | None = None) -> tuple[date, date]:
    """Inclusive start, exclusive end."""

    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    quarter_start = _quarter_start(today)
    ranges = {
        "today": (today, today + timedelta(days=1)),
        "yesterday": (today - timedelta(days=1), today),
        "this_week": (week_start, week_start + timedelta(days=7)),
        "last_week": (week_start - timedelta(days=7), week_start),
        "this_month": (month_start, _add_months(month_start, 1)),
        "last_month": (_add_months(month_start, -1), month_start),
        "this_quarter": (quarter_start, _add_months(quarter_start, 3)),
        "last_quarter": (_add_months(quarter_start, -3), quarter_start),
        "this_year": (date(today.year, 1, 1), date(today.year + 1, 1, 1)),
        "last_year": (date(today.year - 1, 1, 1), date(today.year, 1, 1)),
        "last_7_days": (today - timedelta(days=6), today + timedelta(days=1)),
        "last_30_days": (today - timedelta(days=29), today + timedelta(days=1)),
        "last_90_days": (today - timedelta(days=89), today + timedelta(days=1)),
        "next_7_days": (today, today + timedelta(days=7)),
        "next_30_days": (today, today + timedelta(days=30)),
        "next_90_days": (today, today + timedelta(days=90)),
    }
    if key not in ranges:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown period '{key}'.")
    return ranges[key]


class _Query:
    """Builds column expressions and the joins they need."""

    def __init__(self, definition: ObjectDef):
        self.definition = definition
        self.model = definition.model
        self.joins: dict[str, Any] = {}

    def column(self, field: FieldDef):
        if field.virtual:
            if self.definition.key == "opportunity" and field.key == "stage_category":
                self.joins["stage"] = (PipelineStage, PipelineStage.id == Opportunity.stage_id)
                return PipelineStage.category
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{field.label}' can't be used here.")
        return getattr(self.model, field.key)

    def apply_joins(self, query):
        for target, onclause in self.joins.values():
            query = query.outerjoin(target, onclause)
        return query


def _field(definition: ObjectDef, key: str | None, purpose: str) -> FieldDef:
    field = definition.field(key or "")
    if field is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Choose a valid {purpose} for {definition.label.lower()}.")
    return field


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{value}' isn't a number.") from exc


def _uuid_or_me(value: Any, principal: Principal) -> uuid.UUID | None:
    if value in ("me", "@me"):
        return principal.user_id
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a value from the list.") from exc


def _date_value(value: Any) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{value}' isn't a date (use YYYY-MM-DD).") from exc


def build_condition(q: _Query, flt: ReportFilter, principal: Principal):
    field = _field(q.definition, flt.field, "filter field")
    column = q.column(field)
    op = flt.operator
    value = flt.value
    if op not in OPERATORS + ("in_period",):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown condition '{op}'.")

    if op == "is_empty":
        return or_(column.is_(None), cast(column, String) == "") if field.type in {"string", "enum"} else column.is_(None)
    if op == "is_not_empty":
        return and_(column.is_not(None), cast(column, String) != "") if field.type in {"string", "enum"} else column.is_not(None)

    if field.type in {"date", "datetime"}:
        if op == "in_period":
            start, end = period_range(str(value))
        else:
            day = _date_value(value)
            start, end = day, day + timedelta(days=1)
        if field.type == "datetime":
            start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
            end_dt = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)
            lower, upper = start_dt, end_dt
        else:
            lower, upper = start, end
        if op in {"in_period", "equals"}:
            return and_(column >= lower, column < upper)
        if op == "not_equals":
            return or_(column < lower, column >= upper, column.is_(None))
        if op == "greater_than":
            return column >= upper
        if op == "less_than":
            return column < lower
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"'{op}' doesn't apply to dates.")

    if op == "in_period":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Relative dates only apply to date fields.")

    if field.type in {"number", "currency"}:
        if op == "in":
            return column.in_([_number(v.strip()) for v in str(value).split(",") if v.strip()])
        number = _number(value)
        return {
            "equals": column == number,
            "not_equals": column != number,
            "greater_than": column > number,
            "less_than": column < number,
        }.get(op, column == number)

    if field.type == "bool":
        truthy = str(value).lower() in {"true", "yes", "1"}
        target = column.is_(True) if truthy else or_(column.is_(False), column.is_(None))
        return not_(target) if op == "not_equals" else target

    if field.type in {"user", "ref"}:
        if op == "in":
            return column.in_([_uuid_or_me(v.strip(), principal) for v in str(value).split(",") if v.strip()])
        target = _uuid_or_me(value, principal)
        return column != target if op == "not_equals" else column == target

    text = str(value if value is not None else "")
    lowered = func.lower(cast(column, String))
    if op == "equals":
        return lowered == text.lower()
    if op == "not_equals":
        return or_(lowered != text.lower(), column.is_(None))
    if op == "contains":
        return column.ilike(f"%{text}%")
    if op == "not_contains":
        return or_(not_(column.ilike(f"%{text}%")), column.is_(None))
    if op == "starts_with":
        return column.ilike(f"{text}%")
    if op == "in":
        return lowered.in_([v.strip().lower() for v in text.split(",") if v.strip()])
    if op in {"greater_than", "less_than"}:
        return column > text if op == "greater_than" else column < text
    return true()


def _scope(q: _Query, principal: Principal):
    owner = q.definition.owner_field
    if owner and not principal.can("records.view_all"):
        return getattr(q.model, owner) == principal.user_id
    return true()


def _label_enum(value: Any) -> str:
    return str(value).replace("_", " ").capitalize() if value not in (None, "") else "(none)"


def _date_label(value: Any, granularity: str) -> str:
    if value is None:
        return "(none)"
    day = value.date() if isinstance(value, datetime) else value
    if granularity == "day":
        return day.strftime("%b %-d, %Y")
    if granularity == "week":
        return f"Week of {day.strftime('%b %-d')}"
    if granularity == "month":
        return day.strftime("%b %Y")
    if granularity == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    return str(day.year)


async def _labels(db: AsyncSession, principal: Principal, field: FieldDef, values: list[Any]) -> dict[str, str]:
    if field.type == "user":
        return await names_for(db, principal.organization_id, "user", values)
    if field.type == "ref" and field.ref:
        return await names_for(db, principal.organization_id, field.ref, values)
    return {}


async def run_report(db: AsyncSession, principal: Principal, spec: ReportSpec) -> dict[str, Any]:
    definition = OBJECTS.get(spec.object_type)
    if definition is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose what to report on.")
    if spec.aggregate not in AGGREGATES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose count, sum, average, min or max.")

    q = _Query(definition)
    conditions = [q.model.organization_id == principal.organization_id, _scope(q, principal)]
    conditions += [build_condition(q, flt, principal) for flt in spec.filters]

    count_query = q.apply_joins(select(func.count()).select_from(q.model)).where(*conditions)
    total = (await db.execute(count_query)).scalar_one()

    aggregate_field = None
    aggregate_column = None
    if spec.aggregate != "count":
        aggregate_field = _field(definition, spec.aggregate_field, "number field to total")
        if aggregate_field.type not in {"number", "currency"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{aggregate_field.label} isn't a number.")
        aggregate_column = q.column(aggregate_field)

    def aggregate_expression():
        if spec.aggregate == "count":
            return func.count()
        return getattr(func, spec.aggregate)(aggregate_column)

    summary_value = (
        await db.execute(q.apply_joins(select(aggregate_expression()).select_from(q.model)).where(*conditions))
    ).scalar_one()

    result: dict[str, Any] = {
        "object_type": definition.key,
        "object_label": definition.label,
        "total_count": total,
        "aggregate": spec.aggregate,
        "aggregate_field": aggregate_field.to_dict() if aggregate_field else None,
        "summary": {"value": jsonable(summary_value) or 0, "count": total},
        "groups": [],
        "group_by": None,
        "columns": [],
        "rows": [],
    }

    # ---------------------------------------------------------------- groups
    if spec.group_by:
        group_field = _field(definition, spec.group_by, "grouping field")
        group_column = q.column(group_field)
        granularity = None
        if group_field.type in {"date", "datetime"}:
            granularity = spec.date_granularity if spec.date_granularity in GRANULARITIES else "month"
            group_column = cast(func.date_trunc(granularity, group_column), Date)
        group_query = (
            q.apply_joins(select(group_column.label("g"), aggregate_expression().label("v"), func.count().label("c")).select_from(q.model))
            .where(*conditions)
            .group_by(group_column)
        )
        group_query = group_query.order_by(group_column.asc().nulls_last()) if granularity else group_query.order_by(func.coalesce(aggregate_expression(), 0).desc())
        groups = (await db.execute(group_query.limit(60))).all()
        labels = await _labels(db, principal, group_field, [g for g, _, _ in groups])
        result["group_by"] = group_field.to_dict() | {"granularity": granularity}
        result["groups"] = [
            {
                "key": jsonable(g),
                "label": (
                    _date_label(g, granularity) if granularity
                    else labels.get(str(g), "(none)" if g is None else "Unknown") if group_field.type in {"user", "ref"}
                    else ("Yes" if g else "No") if group_field.type == "bool"
                    else _label_enum(g)
                ),
                "value": float(v or 0),
                "count": c,
            }
            for g, v, c in groups
        ]

    # ------------------------------------------------------------------ rows
    columns = [
        _field(definition, key, "column")
        for key in (spec.columns or list(definition.default_columns))
        if definition.field(key) is not None
    ]
    result["columns"] = [c.to_dict() for c in columns]
    expressions = [q.column(c).label(c.key) for c in columns]
    row_query = q.apply_joins(select(q.model.id.label("__id"), *expressions).select_from(q.model)).where(*conditions)

    sort_field = definition.field(spec.sort_field or "") or definition.field("created_at")
    if sort_field is not None:
        sort_column = q.column(sort_field)
        row_query = row_query.order_by(sort_column.desc().nulls_last() if spec.sort_direction != "asc" else sort_column.asc().nulls_last())
    rows = (await db.execute(row_query.limit(min(spec.row_limit, MAX_ROWS)))).mappings().all()

    label_maps = {}
    for column in columns:
        if column.type in {"user", "ref"}:
            label_maps[column.key] = await _labels(db, principal, column, [row[column.key] for row in rows])

    def present(column: FieldDef, value: Any) -> Any:
        if column.type in {"user", "ref"}:
            return label_maps[column.key].get(str(value)) if value is not None else None
        return jsonable(value)

    result["rows"] = [
        {
            "id": str(row["__id"]),
            "link": definition.link(_Link(row["__id"])),
            "values": {column.key: present(column, row[column.key]) for column in columns},
            "raw": {column.key: jsonable(row[column.key]) for column in columns if column.type in {"user", "ref"}},
        }
        for row in rows
    ]
    return result


class _Link:
    """Minimal stand-in so ObjectDef.link(obj) can build URLs from an id."""

    def __init__(self, record_id: uuid.UUID):
        self.id = record_id
