"""The read-only analytics feed behind the Power BI and Looker Studio integrations.

* Datasets are flat tables with a fixed, documented schema (column name, type,
  description). Custom fields are added as ``cf_<key>`` columns; encrypted custom
  fields and free-text bodies (notes, descriptions, email bodies) are never included.
* Rows come back in pages ordered by ``(updated_at, id)``. ``cursor`` continues where
  the last page stopped and ``updated_since`` asks only for rows changed since a time,
  so refreshes can be incremental. Dates are ISO 8601 in UTC.
* Access is the caller's own: API keys act as the person who created them, so the
  workspace's record visibility (team or hierarchy) applies to leads, contacts,
  accounts and deals, and to activities, tasks, cases, quotes and invoices through
  their owner or linked records.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.core import IntegrationError

MAX_LIMIT = 5000
DEFAULT_LIMIT = 1000
TYPES = ("text", "integer", "number", "boolean", "date", "datetime")


@dataclass(frozen=True)
class Column:
    name: str
    type: str = "text"
    description: str = ""
    source: str | None = None  # attribute on the model, when it differs from the name


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    description: str
    model: str  # import path "module:Class"
    columns: tuple[Column, ...]
    custom_object: str | None = None  # custom fields of this object type become cf_ columns
    owner_columns: tuple[str, ...] = ()  # for visibility, when the model isn't restricted itself
    extra: Callable[..., Any] | None = field(default=None, compare=False)


def _id(name: str, description: str = "") -> Column:
    return Column(name, "text", description)


COMMON_TAIL = (Column("created_at", "datetime", "When the record was created."), Column("updated_at", "datetime", "When the record last changed."))

DATASETS: dict[str, Dataset] = {
    d.key: d
    for d in (
        Dataset(
            "leads", "Leads", "Every lead with its status, source, score and campaign attribution.", "app.models.lead:Lead",
            (
                _id("id"), Column("first_name"), Column("last_name"), Column("email"), Column("phone"), Column("company_name"), Column("job_title"),
                Column("source", description="Where the lead came from (web_form, google_ads, import...)."),
                Column("status", description="new, contacted, qualified, unqualified, nurture or converted."),
                Column("score", "integer"), _id("owner_user_id", "Joins to users.id."), _id("territory_id"), Column("email_opt_out", "boolean"),
                Column("converted_at", "datetime"), _id("converted_account_id"), _id("converted_contact_id"), _id("converted_opportunity_id", "Joins to opportunities.id."),
                Column("utm_source", description="From the form or ad that created the lead."), Column("utm_medium"), Column("utm_campaign"),
                Column("google_campaign_id", description="The Google Ads campaign, when known."),
                *COMMON_TAIL,
            ),
            custom_object="lead",
        ),
        Dataset(
            "contacts", "Contacts", "People at your accounts.", "app.models.contact:Contact",
            (
                _id("id"), _id("account_id", "Joins to accounts.id."), _id("owner_user_id"), Column("first_name"), Column("last_name"), Column("email"),
                Column("phone"), Column("mobile"), Column("job_title"), Column("department"), Column("segment"), Column("stage"), Column("score", "integer"),
                Column("is_primary", "boolean"), Column("is_active", "boolean"), Column("is_archived", "boolean"), Column("email_opt_out", "boolean"),
                *COMMON_TAIL,
            ),
            custom_object="contact",
        ),
        Dataset(
            "accounts", "Accounts", "Companies and their firmographics.", "app.models.account:Account",
            (
                _id("id"), Column("name"), Column("domain"), Column("website"), Column("industry"), Column("phone"), Column("lifecycle_stage"),
                Column("employee_count", "integer"), Column("annual_revenue", "number"), Column("billing_city"), Column("billing_state"),
                Column("billing_country"), _id("owner_user_id"), _id("parent_account_id"), _id("territory_id"), Column("score", "integer"),
                Column("is_archived", "boolean"), *COMMON_TAIL,
            ),
            custom_object="account",
        ),
        Dataset(
            "opportunities", "Deals", "Deals with their pipeline stage, amount and dates.", "app.models.opportunity:Opportunity",
            (
                _id("id"), Column("name"), _id("account_id"), _id("primary_contact_id"), _id("pipeline_id"), _id("stage_id", "Joins to pipeline_stages.id."),
                Column("stage_name", description="The stage's name."), Column("stage_category", description="open, won or lost."),
                _id("owner_user_id"), Column("amount", "number"), Column("currency"), Column("probability", "integer"),
                Column("expected_close_date", "date"), Column("closed_at", "datetime"), Column("opportunity_type"), Column("lead_source"),
                Column("priority"), Column("forecast_category"), Column("loss_reason"), Column("competitor"), Column("stage_entered_at", "datetime"),
                *COMMON_TAIL,
            ),
            custom_object="opportunity",
        ),
        Dataset(
            "activities", "Activities", "Calls, emails, meetings and notes logged on records (without their text).", "app.models.activity:Activity",
            (
                _id("id"), Column("activity_type"), Column("subject"), Column("direction"), Column("occurred_at", "datetime"),
                _id("account_id"), _id("contact_id"), _id("lead_id"), _id("opportunity_id"), _id("created_by_user_id"), *COMMON_TAIL,
            ),
            owner_columns=("created_by_user_id",),
        ),
        Dataset(
            "tasks", "Tasks", "Tasks with their status, priority and due dates.", "app.models.task:Task",
            (
                _id("id"), Column("title"), Column("task_type"), Column("status"), Column("priority"), Column("source"),
                Column("due_at", "datetime"), Column("completed_at", "datetime"), _id("assigned_to_user_id"), _id("created_by_user_id"),
                _id("account_id"), _id("contact_id"), _id("lead_id"), _id("opportunity_id"), *COMMON_TAIL,
            ),
            owner_columns=("assigned_to_user_id", "created_by_user_id"),
        ),
        Dataset(
            "cases", "Cases", "Support cases with SLA dates and satisfaction.", "app.models.platform:Case",
            (
                _id("id"), Column("number", "integer"), Column("subject"), Column("status"), Column("priority"), Column("channel"), Column("category"),
                _id("account_id"), _id("contact_id"), _id("assigned_to_user_id"), _id("created_by_user_id"),
                Column("first_response_due_at", "datetime"), Column("resolution_due_at", "datetime"), Column("first_responded_at", "datetime"),
                Column("resolved_at", "datetime"), Column("closed_at", "datetime"), Column("satisfaction_rating", "integer"), *COMMON_TAIL,
            ),
            custom_object="case",
            owner_columns=("assigned_to_user_id", "created_by_user_id"),
        ),
        Dataset(
            "quotes", "Quotes", "Quotes with their totals and status.", "app.models.platform:Quote",
            (
                _id("id"), Column("number", "integer"), Column("name"), Column("status"), _id("opportunity_id"), _id("account_id"), _id("contact_id"),
                _id("owner_user_id"), Column("valid_until", "date"), Column("currency"), Column("subtotal", "number"), Column("discount_total", "number"),
                Column("tax_total", "number"), Column("total", "number"), Column("sent_at", "datetime"), Column("accepted_at", "datetime"),
                Column("declined_at", "datetime"), *COMMON_TAIL,
            ),
            owner_columns=("owner_user_id",),
        ),
        Dataset(
            "sales_invoices", "Invoices", "Invoices with amounts, payments and due dates.", "app.models.editions:SalesInvoice",
            (
                _id("id"), Column("number", "integer"), Column("status", description="draft, sent, paid or void."), _id("quote_id"), _id("account_id"),
                _id("contact_id"), _id("sales_order_id"), _id("opportunity_id"), _id("owner_user_id"), Column("issue_date", "date"),
                Column("due_date", "date"), Column("currency"), Column("subtotal", "number"), Column("discount_total", "number"),
                Column("tax_total", "number"), Column("total", "number"), Column("amount_paid", "number"), Column("paid_at", "datetime"),
                *COMMON_TAIL,
            ),
            owner_columns=("owner_user_id",),
        ),
        Dataset(
            "users", "Users", "People in the workspace, to show owner names.", "app.models.membership:OrganizationMembership",
            (
                _id("id", "The person's user ID (owner_user_id elsewhere)."), Column("full_name"), Column("email"), Column("role"),
                _id("team_id"), Column("status"), Column("job_title"), *COMMON_TAIL,
            ),
        ),
        Dataset(
            "pipeline_stages", "Pipeline stages", "Stages of every pipeline, in order.", "app.models.pipeline_stage:PipelineStage",
            (
                _id("id"), _id("pipeline_id"), Column("pipeline_name"), Column("name"), Column("position", "integer"),
                Column("probability", "integer"), Column("category", description="open, won or lost."), *COMMON_TAIL,
            ),
        ),
    )
}


def _model(dataset: Dataset) -> Any:
    import importlib

    module, name = dataset.model.split(":")
    return getattr(importlib.import_module(module), name)


# ---------------------------------------------------------------------------
# Schema (with the workspace's custom fields)
# ---------------------------------------------------------------------------

CUSTOM_TYPES = {"number": "number", "currency": "number", "percent": "number", "checkbox": "boolean", "date": "date", "datetime": "datetime", "auto_number": "text"}


async def custom_columns(db: AsyncSession, organization_id: uuid.UUID, dataset: Dataset) -> list[tuple[Column, str]]:
    if not dataset.custom_object:
        return []
    from app.services.custom_fields import fields_for

    out = []
    for item in await fields_for(db, organization_id, dataset.custom_object):
        if item.is_encrypted or item.field_type == "formula":
            continue
        out.append((Column(f"cf_{item.key}", CUSTOM_TYPES.get(item.field_type, "text"), item.label), item.key))
    return out


async def schema(db: AsyncSession, organization_id: uuid.UUID, key: str) -> dict[str, Any]:
    dataset = get(key)
    custom = await custom_columns(db, organization_id, dataset)
    return {
        "dataset": dataset.key,
        "label": dataset.label,
        "description": dataset.description,
        "columns": [{"name": c.name, "type": c.type, "description": c.description} for c in [*dataset.columns, *(c for c, _ in custom)]],
    }


def get(key: str) -> Dataset:
    dataset = DATASETS.get(key)
    if dataset is None:
        raise IntegrationError(f"Unknown dataset {key!r}. Use one of: {', '.join(DATASETS)}.", 404)
    return dataset


# ---------------------------------------------------------------------------
# Cursors
# ---------------------------------------------------------------------------


def encode_cursor(updated_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([updated_at.astimezone(timezone.utc).isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return datetime.fromisoformat(stamp), uuid.UUID(row_id)
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is the caller's mistake
        raise IntegrationError("That cursor isn't valid. Start again without one.", 422) from exc


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationError("updated_since must be an ISO 8601 date or time, like 2026-01-31T00:00:00Z.", 422) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def plain(value: Any, kind: str) -> Any:
    if value is None or value == "":
        return None
    if kind == "datetime":
        if isinstance(value, datetime):
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            return plain(datetime.fromisoformat(str(value).replace("Z", "+00:00")), "datetime")
        except ValueError:
            return None
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            return None
    if kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if kind == "number":
        try:
            return float(value) if not isinstance(value, Decimal) else float(value)
        except (TypeError, ValueError):
            return None
    if kind == "boolean":
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes"}
        return bool(value)
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)[:2000]
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)[:2000]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _visible_filter(dataset: Dataset, model: Any, organization_id: uuid.UUID, owners: tuple[uuid.UUID, ...]) -> Any:
    """For people limited to their own (or their team's) records."""

    from app.models.account import Account
    from app.models.contact import Contact
    from app.models.lead import Lead
    from app.models.opportunity import Opportunity

    def visible(target: Any) -> Any:
        return select(target.id).where(target.organization_id == organization_id, or_(target.owner_user_id.in_(owners), target.owner_user_id.is_(None)))

    conditions = [getattr(model, column).in_(owners) for column in dataset.owner_columns]
    if dataset.key in {"quotes", "sales_invoices"}:
        conditions.append(model.owner_user_id.is_(None))
    if dataset.key in {"activities", "tasks"}:
        for column, target in (("account_id", Account), ("contact_id", Contact), ("lead_id", Lead), ("opportunity_id", Opportunity)):
            conditions.append(getattr(model, column).in_(visible(target)))
    if dataset.key == "cases":
        conditions.append(model.account_id.in_(visible(Account)))
        conditions.append(model.contact_id.in_(visible(Contact)))
    return or_(*conditions) if conditions else None


async def page(
    db: AsyncSession,
    organization_id: uuid.UUID,
    key: str,
    *,
    cursor: str | None = None,
    updated_since: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    dataset = get(key)
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    custom = await custom_columns(db, organization_id, dataset)
    owners = (db.sync_session.info.get("visible_owners") if hasattr(db, "sync_session") else None)

    if dataset.key == "users":
        return await _users_page(db, organization_id, dataset, cursor=cursor, updated_since=updated_since, limit=limit)
    model = _model(dataset)
    query = select(model).where(model.organization_id == organization_id)
    if owners is not None and dataset.key not in {"leads", "contacts", "accounts", "opportunities", "pipeline_stages"}:
        restriction = _visible_filter(dataset, model, organization_id, tuple(owners))
        if restriction is not None:
            query = query.where(restriction)
    if updated_since is not None:
        query = query.where(model.updated_at > updated_since)
    if cursor:
        after_time, after_id = decode_cursor(cursor)
        query = query.where(or_(model.updated_at > after_time, and_(model.updated_at == after_time, model.id > after_id)))
    rows = (await db.execute(query.order_by(model.updated_at.asc(), model.id.asc()).limit(limit + 1))).scalars().all()
    more = len(rows) > limit
    rows = rows[:limit]

    stages: dict[str, dict[str, Any]] = {}
    pipelines: dict[str, str] = {}
    if dataset.key in {"opportunities", "pipeline_stages"}:
        from app.models.pipeline import Pipeline
        from app.models.pipeline_stage import PipelineStage

        stages = {
            str(s.id): {"name": s.name, "category": s.category}
            for s in (await db.execute(select(PipelineStage).where(PipelineStage.organization_id == organization_id))).scalars()
        }
        pipelines = {str(p.id): p.name for p in (await db.execute(select(Pipeline).where(Pipeline.organization_id == organization_id))).scalars()}

    items = []
    for row in rows:
        item: dict[str, Any] = {}
        attribution = {}
        fields = getattr(row, "custom_fields", None)
        if isinstance(fields, dict) and isinstance(fields.get("_attribution"), dict):
            attribution = fields["_attribution"]
        for column in dataset.columns:
            if column.name == "stage_name":
                value = (stages.get(str(row.stage_id)) or {}).get("name")
            elif column.name == "stage_category":
                value = (stages.get(str(row.stage_id)) or {}).get("category")
            elif column.name == "pipeline_name":
                value = pipelines.get(str(row.pipeline_id))
            elif dataset.key == "leads" and column.name in {"utm_source", "utm_medium", "utm_campaign", "google_campaign_id"}:
                value = attribution.get(column.name)
            else:
                value = getattr(row, column.source or column.name, None)
            item[column.name] = plain(value, column.type)
        if custom:
            from app.core.crypto import is_encrypted

            stored = fields if isinstance(fields, dict) else {}
            for column, field_key in custom:
                value = stored.get(field_key)
                item[column.name] = None if is_encrypted(value) else plain(value, column.type)
        items.append(item)
    next_cursor = encode_cursor(rows[-1].updated_at, rows[-1].id) if more and rows else None
    return {"dataset": dataset.key, "count": len(items), "items": items, "next_cursor": next_cursor}


async def _users_page(db: AsyncSession, organization_id: uuid.UUID, dataset: Dataset, *, cursor: str | None, updated_since: datetime | None, limit: int) -> dict[str, Any]:
    from app.models.membership import OrganizationMembership
    from app.models.user import User

    query = (
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == organization_id)
    )
    if updated_since is not None:
        query = query.where(OrganizationMembership.updated_at > updated_since)
    if cursor:
        after_time, after_id = decode_cursor(cursor)
        query = query.where(
            or_(OrganizationMembership.updated_at > after_time, and_(OrganizationMembership.updated_at == after_time, OrganizationMembership.id > after_id))
        )
    rows = (await db.execute(query.order_by(OrganizationMembership.updated_at.asc(), OrganizationMembership.id.asc()).limit(limit + 1))).all()
    more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "role": membership.role,
            "team_id": plain(membership.team_id, "text"),
            "status": membership.status,
            "job_title": user.job_title,
            "created_at": plain(membership.created_at, "datetime"),
            "updated_at": plain(membership.updated_at, "datetime"),
        }
        for membership, user in rows
    ]
    next_cursor = encode_cursor(rows[-1][0].updated_at, rows[-1][0].id) if more and rows else None
    return {"dataset": dataset.key, "count": len(items), "items": items, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Power Query (Power BI) and the Looker Studio community connector
# ---------------------------------------------------------------------------

M_TYPES = {"text": "type text", "integer": "Int64.Type", "number": "type number", "boolean": "type logical", "date": "type date", "datetime": "type datetimezone"}


def power_query_function(base_url: str) -> str:
    """A Power Query function that pages through one dataset and types its columns."""

    return f'''// AIVA feed: paste into Power BI Desktop → Get data → Blank query → Advanced editor,
// and name the query AivaFeed. Create a text parameter named AivaApiKey holding a
// read-only AIVA API key (Manage parameters → New parameter).
(dataset as text, optional updatedSince as nullable text) as table =>
let
    BaseUrl = "{base_url.rstrip('/')}",
    Headers = [Authorization = "Bearer " & AivaApiKey, Accept = "application/json"],
    Schema = Json.Document(Web.Contents(BaseUrl, [RelativePath = "api/aiva/analytics-feed/" & dataset & "/schema", Headers = Headers])),
    Columns = List.Transform(Schema[columns], each _[name]),
    Types = List.Transform(Schema[columns], each {{_[name],
        if _[type] = "integer" then Int64.Type
        else if _[type] = "number" then type number
        else if _[type] = "boolean" then type logical
        else if _[type] = "date" then type date
        else if _[type] = "datetime" then type datetimezone
        else type text}}),
    GetPage = (cursor as nullable text) =>
        let
            Query = [limit = "5000"]
                & (if cursor = null then [] else [cursor = cursor])
                & (if updatedSince = null then [] else [updated_since = updatedSince]),
            Page = Json.Document(Web.Contents(BaseUrl, [RelativePath = "api/aiva/analytics-feed/" & dataset, Query = Query, Headers = Headers]))
        in
            Page,
    Pages = List.Generate(
        () => GetPage(null),
        each _ <> null,
        each if _[next_cursor] = null then null else GetPage(_[next_cursor]),
        each _[items]
    ),
    Rows = List.Combine(Pages),
    Table = Table.FromRecords(Rows, Columns, MissingField.UseNull),
    Typed = Table.TransformColumnTypes(Table, Types)
in
    Typed
'''


def power_query_examples() -> str:
    return "\n".join(f'// {d.label}: new Blank query →  = AivaFeed("{d.key}")' for d in DATASETS.values())


def looker_connector_files(base_url: str, logo_url: str) -> dict[str, str]:
    """Code.gs, appsscript.json and README.md for an Apps Script community connector."""

    base = base_url.rstrip("/")
    datasets = json.dumps([{"key": d.key, "label": d.label} for d in DATASETS.values()])
    code = f'''/**
 * AIVA CRM community connector for Looker Studio.
 * Reads the AIVA analytics feed with a read-only API key (Settings → API keys).
 */
var AIVA_BASE_URL = "{base}";
var DATASETS = {datasets};
var MAX_ROWS = 200000;

var cc = DataStudioApp.createCommunityConnector();

function getAuthType() {{
  return cc.newAuthTypeResponse().setAuthType(cc.AuthType.KEY).setHelpUrl(AIVA_BASE_URL + "/settings/api-keys").build();
}}

function isAdminUser() {{
  return false;
}}

function _key() {{
  return PropertiesService.getUserProperties().getProperty("aiva.key");
}}

function _fetch(path, key) {{
  var response = UrlFetchApp.fetch(AIVA_BASE_URL + "/api/aiva/analytics-feed" + path, {{
    method: "get",
    muteHttpExceptions: true,
    headers: {{ Authorization: "Bearer " + key, Accept: "application/json" }}
  }});
  var code = response.getResponseCode();
  if (code >= 400) {{
    var message = "AIVA answered " + code;
    try {{ message = JSON.parse(response.getContentText()).detail || message; }} catch (e) {{}}
    throw new Error(message);
  }}
  return JSON.parse(response.getContentText());
}}

function setCredentials(request) {{
  var key = request.key;
  try {{
    _fetch("", key);
  }} catch (e) {{
    return {{ errorCode: "INVALID_CREDENTIALS" }};
  }}
  PropertiesService.getUserProperties().setProperty("aiva.key", key);
  return {{ errorCode: "NONE" }};
}}

function isAuthValid() {{
  var key = _key();
  if (!key) return false;
  try {{
    _fetch("", key);
    return true;
  }} catch (e) {{
    return false;
  }}
}}

function resetAuth() {{
  PropertiesService.getUserProperties().deleteProperty("aiva.key");
}}

function getConfig(request) {{
  var config = cc.getConfig();
  config.newInfo().setId("about").setText("Pick the AIVA dataset for this data source. Add one data source per dataset and blend them on the ID columns.");
  var select = config.newSelectSingle().setId("dataset").setName("Dataset").setAllowOverride(false);
  DATASETS.forEach(function (d) {{
    select.addOption(config.newOptionBuilder().setLabel(d.label).setValue(d.key));
  }});
  return config.build();
}}

function _dataset(request) {{
  var dataset = (request.configParams || {{}}).dataset;
  if (!dataset) cc.newUserError().setText("Pick a dataset in the data source settings.").throwException();
  return dataset;
}}

function _fields(schema) {{
  var fields = cc.getFields();
  var types = cc.FieldType;
  schema.columns.forEach(function (column) {{
    var isMetric = column.type === "number" || (column.type === "integer" && column.name !== "number" && column.name !== "position");
    var field = isMetric ? fields.newMetric() : fields.newDimension();
    field.setId(column.name).setName(column.name).setDescription(column.description || "");
    if (column.type === "number" || column.type === "integer") field.setType(types.NUMBER);
    else if (column.type === "boolean") field.setType(types.BOOLEAN);
    else if (column.type === "date") field.setType(types.YEAR_MONTH_DAY);
    else if (column.type === "datetime") field.setType(types.YEAR_MONTH_DAY_SECOND);
    else field.setType(types.TEXT);
  }});
  return fields;
}}

function getSchema(request) {{
  var schema = _fetch("/" + _dataset(request) + "/schema", _key());
  return {{ schema: _fields(schema).build() }};
}}

function _value(value, type) {{
  if (value === null || value === undefined) return null;
  if (type === "date") return String(value).replace(/-/g, "").slice(0, 8);
  if (type === "datetime") return String(value).replace(/[-:T]/g, "").slice(0, 14);
  return value;
}}

function getData(request) {{
  var dataset = _dataset(request);
  var key = _key();
  var schema = _fetch("/" + dataset + "/schema", key);
  var types = {{}};
  schema.columns.forEach(function (c) {{ types[c.name] = c.type; }});
  var wanted = request.fields.map(function (f) {{ return f.name; }});
  var requested = _fields(schema).forIds(wanted);
  var rows = [];
  var cursor = null;
  do {{
    var page = _fetch("/" + dataset + "?limit=5000" + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""), key);
    page.items.forEach(function (item) {{
      rows.push({{ values: wanted.map(function (name) {{ return _value(item[name], types[name]); }}) }});
    }});
    cursor = page.next_cursor;
  }} while (cursor && rows.length < MAX_ROWS);
  return {{ schema: requested.build(), rows: rows }};
}}
'''
    manifest = json.dumps(
        {
            "timeZone": "Etc/UTC",
            "dependencies": {},
            "exceptionLogging": "STACKDRIVER",
            "runtimeVersion": "V8",
            "urlFetchWhitelist": [f"{base}/"],
            "oauthScopes": ["https://www.googleapis.com/auth/script.external_request"],
            "dataStudio": {
                "name": "AIVA CRM",
                "company": "AIVA",
                "companyUrl": base,
                "logoUrl": logo_url,
                "addonUrl": base,
                "supportUrl": base,
                "description": "Leads, contacts, accounts, deals, activities, tasks, cases, quotes and invoices from your AIVA CRM workspace.",
                "authType": ["KEY"],
            },
        },
        indent=2,
    )
    readme = f"""# AIVA CRM connector for Looker Studio

1. In AIVA, create a **read-only** API key (Settings → API keys). The connector acts as the person who created it.
2. Go to https://script.google.com and create a new project named "AIVA CRM connector".
3. Replace the contents of `Code.gs` with the `Code.gs` in this folder.
4. Project Settings → tick **Show "appsscript.json" manifest file in editor**, then replace `appsscript.json` with the one here.
5. Deploy → **Test deployments** → copy the **Head deployment ID**.
6. Open https://lookerstudio.google.com/datasources/create?connectorId=HEAD_DEPLOYMENT_ID (paste the ID), authorize, paste the API key, and pick a dataset.
7. Add one data source per dataset and blend them on the ID columns (for example `opportunities.owner_user_id` with `users.id`).

The connector reads {base}/api/aiva/analytics-feed with your key. Encrypted custom fields and free-text notes are never included.
To share the connector with your organization, publish a production deployment (Deploy → New deployment → type Add-on).
"""
    return {"Code.gs": code, "appsscript.json": manifest, "README.md": readme}
