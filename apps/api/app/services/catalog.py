"""Object and field catalog.

One description of every reportable object drives the report builder,
workflow conditions and actions, lead scoring rules, and the audit trail,
so the UI and API agree on field names, types and allowed values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.account import Account
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.lead import Lead
from app.models.opportunity import Opportunity
from app.models.platform import Case, KnowledgeArticle, Product, Quote
from app.models.task import Task

LEAD_STATUS = ["new", "contacted", "qualified", "unqualified", "nurture", "converted"]
LIFECYCLE = ["prospect", "lead", "customer", "partner", "inactive", "churned"]
OPP_TYPE = ["new_business", "renewal", "upsell", "cross_sell", "expansion"]
OPP_PRIORITY = ["low", "medium", "high", "critical"]
FORECAST = ["pipeline", "best_case", "commit", "closed", "omitted"]
TASK_TYPE = ["general", "call", "email", "meeting", "follow_up", "demo", "proposal", "review", "renewal"]
TASK_STATUS = ["open", "in_progress", "completed", "cancelled"]
TASK_PRIORITY = ["low", "medium", "high", "urgent"]
CASE_STATUS = ["new", "open", "pending", "on_hold", "resolved", "closed"]
CASE_PRIORITY = ["low", "medium", "high", "urgent"]
CASE_CHANNEL = ["email", "phone", "web", "chat", "portal", "social"]
QUOTE_STATUS = ["draft", "sent", "accepted", "declined", "expired"]
ACTIVITY_TYPE = ["email", "call", "meeting", "note", "sms", "task_completed", "document", "system", "ai_action"]
BILLING = ["one_time", "monthly", "quarterly", "annual"]
KB_STATUS = ["draft", "published", "archived"]

# Field types: string, number, currency, date, datetime, enum, bool, user, ref
GROUPABLE_TYPES = {"string", "enum", "bool", "user", "ref", "date", "datetime"}
AGGREGATABLE_TYPES = {"number", "currency"}


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    type: str
    options: tuple[str, ...] = ()
    ref: str | None = None  # for type "ref": account, contact, stage, pipeline, team, opportunity
    updatable: bool = False  # workflow "update field" may set it
    virtual: bool = False  # computed by the report engine (not a column on the record)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "options": list(self.options),
            "ref": self.ref,
            "groupable": self.type in GROUPABLE_TYPES,
            "aggregatable": self.type in AGGREGATABLE_TYPES,
            "updatable": self.updatable,
            "virtual": self.virtual,
        }


@dataclass(frozen=True)
class ObjectDef:
    key: str
    label: str
    singular: str
    model: type
    fields: tuple[FieldDef, ...]
    default_columns: tuple[str, ...]
    owner_field: str | None
    link: Callable[[Any], str]
    label_of: Callable[[Any], str]
    extra: dict[str, Any] = field(default_factory=dict)

    def field(self, key: str) -> FieldDef | None:
        return next((f for f in self.fields if f.key == key), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "singular": self.singular,
            "fields": [f.to_dict() for f in self.fields],
            "default_columns": list(self.default_columns),
            "owner_field": self.owner_field,
        }


def _name(obj: Any) -> str:
    return " ".join(part for part in (getattr(obj, "first_name", ""), getattr(obj, "last_name", "")) if part)


F = FieldDef
CREATED = F("created_at", "Created", "datetime")
UPDATED = F("updated_at", "Last modified", "datetime")

OBJECTS: dict[str, ObjectDef] = {
    "lead": ObjectDef(
        "lead", "Leads", "Lead", Lead,
        (
            F("first_name", "First name", "string"),
            F("last_name", "Last name", "string"),
            F("email", "Email", "string"),
            F("phone", "Phone", "string"),
            F("company_name", "Company", "string"),
            F("job_title", "Job title", "string"),
            F("source", "Source", "string", updatable=True),
            F("status", "Status", "enum", tuple(LEAD_STATUS[:-1]), updatable=True),
            F("score", "Score", "number"),
            F("owner_user_id", "Owner", "user", updatable=True),
            CREATED, UPDATED,
            F("converted_at", "Converted", "datetime"),
        ),
        ("first_name", "last_name", "company_name", "status", "score", "owner_user_id", "created_at"),
        "owner_user_id",
        lambda o: f"/leads/{o.id}",
        lambda o: _name(o) or (o.company_name or "Lead"),
    ),
    "account": ObjectDef(
        "account", "Accounts", "Account", Account,
        (
            F("name", "Name", "string"),
            F("domain", "Domain", "string"),
            F("industry", "Industry", "string", updatable=True),
            F("lifecycle_stage", "Lifecycle stage", "enum", tuple(LIFECYCLE), updatable=True),
            F("employee_count", "Employees", "number"),
            F("annual_revenue", "Annual revenue", "currency"),
            F("billing_city", "City", "string"),
            F("billing_state", "State", "string"),
            F("billing_country", "Country", "string"),
            F("owner_user_id", "Owner", "user", updatable=True),
            F("is_archived", "Archived", "bool"),
            CREATED, UPDATED,
        ),
        ("name", "industry", "lifecycle_stage", "annual_revenue", "billing_country", "owner_user_id"),
        "owner_user_id",
        lambda o: f"/accounts/{o.id}",
        lambda o: o.name,
    ),
    "contact": ObjectDef(
        "contact", "Contacts", "Contact", Contact,
        (
            F("first_name", "First name", "string"),
            F("last_name", "Last name", "string"),
            F("email", "Email", "string"),
            F("phone", "Phone", "string"),
            F("job_title", "Job title", "string"),
            F("department", "Department", "string"),
            F("segment", "Segment", "string", updatable=True),
            F("account_id", "Account", "ref", ref="account"),
            F("owner_user_id", "Owner", "user", updatable=True),
            F("is_primary", "Primary contact", "bool"),
            F("is_archived", "Archived", "bool"),
            CREATED, UPDATED,
        ),
        ("first_name", "last_name", "email", "job_title", "account_id", "owner_user_id"),
        "owner_user_id",
        lambda o: f"/contacts/{o.id}",
        _name,
    ),
    "opportunity": ObjectDef(
        "opportunity", "Opportunities", "Opportunity", Opportunity,
        (
            F("name", "Name", "string"),
            F("account_id", "Account", "ref", ref="account"),
            F("pipeline_id", "Pipeline", "ref", ref="pipeline"),
            F("stage_id", "Stage", "ref", ref="stage"),
            F("stage_category", "Stage outcome", "enum", ("open", "won", "lost"), virtual=True),
            F("amount", "Amount", "currency"),
            F("probability", "Probability (%)", "number"),
            F("expected_close_date", "Close date", "date"),
            F("closed_at", "Closed", "datetime"),
            F("owner_user_id", "Owner", "user", updatable=True),
            F("opportunity_type", "Type", "enum", tuple(OPP_TYPE), updatable=True),
            F("lead_source", "Lead source", "string"),
            F("priority", "Priority", "enum", tuple(OPP_PRIORITY), updatable=True),
            F("forecast_category", "Forecast category", "enum", tuple(FORECAST), updatable=True),
            F("competitor", "Competitor", "string"),
            CREATED, UPDATED,
        ),
        ("name", "account_id", "stage_id", "amount", "expected_close_date", "owner_user_id"),
        "owner_user_id",
        lambda o: f"/opportunities/{o.id}",
        lambda o: o.name,
    ),
    "task": ObjectDef(
        "task", "Tasks", "Task", Task,
        (
            F("title", "Title", "string"),
            F("task_type", "Type", "enum", tuple(TASK_TYPE)),
            F("status", "Status", "enum", tuple(TASK_STATUS), updatable=True),
            F("priority", "Priority", "enum", tuple(TASK_PRIORITY), updatable=True),
            F("due_at", "Due", "datetime"),
            F("completed_at", "Completed", "datetime"),
            F("assigned_to_user_id", "Assigned to", "user", updatable=True),
            F("account_id", "Account", "ref", ref="account"),
            F("is_ai_generated", "AI generated", "bool"),
            CREATED, UPDATED,
        ),
        ("title", "task_type", "status", "priority", "due_at", "assigned_to_user_id"),
        "assigned_to_user_id",
        lambda o: "/tasks",
        lambda o: o.title,
    ),
    "case": ObjectDef(
        "case", "Cases", "Case", Case,
        (
            F("number", "Case number", "number"),
            F("subject", "Subject", "string"),
            F("status", "Status", "enum", tuple(CASE_STATUS), updatable=True),
            F("priority", "Priority", "enum", tuple(CASE_PRIORITY), updatable=True),
            F("channel", "Channel", "enum", tuple(CASE_CHANNEL)),
            F("category", "Category", "string", updatable=True),
            F("account_id", "Account", "ref", ref="account"),
            F("contact_id", "Contact", "ref", ref="contact"),
            F("assigned_to_user_id", "Assigned to", "user", updatable=True),
            F("team_id", "Queue", "ref", ref="team", updatable=True),
            F("first_response_due_at", "First response due", "datetime"),
            F("resolution_due_at", "Resolution due", "datetime"),
            F("resolved_at", "Resolved", "datetime"),
            F("satisfaction_rating", "Satisfaction (1–5)", "number"),
            CREATED, UPDATED,
        ),
        ("number", "subject", "status", "priority", "assigned_to_user_id", "created_at"),
        "assigned_to_user_id",
        lambda o: f"/cases/{o.id}",
        lambda o: f"CS-{o.number}: {o.subject}" if o.number else o.subject,
    ),
    "quote": ObjectDef(
        "quote", "Quotes", "Quote", Quote,
        (
            F("number", "Quote number", "number"),
            F("name", "Name", "string"),
            F("status", "Status", "enum", tuple(QUOTE_STATUS), updatable=True),
            F("account_id", "Account", "ref", ref="account"),
            F("opportunity_id", "Opportunity", "ref", ref="opportunity"),
            F("owner_user_id", "Owner", "user", updatable=True),
            F("subtotal", "Subtotal", "currency"),
            F("discount_total", "Discount", "currency"),
            F("total", "Total", "currency"),
            F("valid_until", "Valid until", "date"),
            F("accepted_at", "Accepted", "datetime"),
            CREATED, UPDATED,
        ),
        ("number", "name", "status", "account_id", "total", "valid_until"),
        "owner_user_id",
        lambda o: f"/quotes/{o.id}",
        lambda o: f"Q-{o.number}: {o.name}" if o.number else o.name,
    ),
    "activity": ObjectDef(
        "activity", "Activities", "Activity", Activity,
        (
            F("activity_type", "Type", "enum", tuple(ACTIVITY_TYPE)),
            F("subject", "Subject", "string"),
            F("direction", "Direction", "enum", ("inbound", "outbound")),
            F("occurred_at", "Occurred", "datetime"),
            F("account_id", "Account", "ref", ref="account"),
            F("contact_id", "Contact", "ref", ref="contact"),
            F("created_by_user_id", "Logged by", "user"),
            CREATED,
        ),
        ("activity_type", "subject", "account_id", "occurred_at", "created_by_user_id"),
        "created_by_user_id",
        lambda o: "/activities",
        lambda o: o.subject,
    ),
    "product": ObjectDef(
        "product", "Products", "Product", Product,
        (
            F("name", "Name", "string"),
            F("sku", "SKU", "string"),
            F("family", "Family", "string"),
            F("unit_price", "Unit price", "currency"),
            F("billing_frequency", "Billing", "enum", tuple(BILLING)),
            F("is_active", "Active", "bool"),
            CREATED,
        ),
        ("name", "sku", "family", "unit_price", "billing_frequency", "is_active"),
        None,
        lambda o: "/products",
        lambda o: o.name,
    ),
}

# Objects that are audited but not reportable on their own.
AUDIT_ONLY = {KnowledgeArticle: ("kb_article", lambda o: o.title)}

MODEL_TO_OBJECT: dict[type, str] = {definition.model: key for key, definition in OBJECTS.items()}


def object_for_instance(obj: Any) -> ObjectDef | None:
    key = MODEL_TO_OBJECT.get(type(obj))
    return OBJECTS.get(key) if key else None


def catalog() -> list[dict[str, Any]]:
    return [definition.to_dict() for definition in OBJECTS.values()]
