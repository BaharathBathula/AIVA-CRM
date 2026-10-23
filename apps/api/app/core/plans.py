"""Plans, prices and the features each plan unlocks.

AIVA is sold per user per month. Every new workspace starts on a seven-day
free trial with everything switched on; after that the workspace picks
Starter, Pro or Enterprise. Each feature has a stable key used by API gates
(:func:`app.api.dependencies.features.require_feature`) and by the web app
to show upgrade prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

TRIAL_DAYS = 7

PLAN_ORDER = ("trial", "starter", "pro", "enterprise")
PAID_PLANS = ("starter", "pro", "enterprise")


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    plan: str  # lowest paid plan that includes it
    description: str


FEATURES: tuple[Feature, ...] = (
    # ---------------------------------------------------------------- Starter
    Feature("contact_management", "Contact management", "starter", "Leads, accounts and contacts with owners, segments and history."),
    Feature("follow_up_reminders", "Follow-up reminders", "starter", "Reminders on tasks and follow-ups, delivered in the app and by email."),
    Feature("workflow_automation", "Workflow automation", "starter", "When-then rules that create tasks, update fields and notify people."),
    Feature("email_templates", "Custom email templates", "starter", "Reusable emails with merge fields such as {{first_name}}."),
    Feature("tasks_meetings_calls", "Tasks, meetings & calls", "starter", "Plan your day and log every touchpoint."),
    Feature("data_import_export", "Data import and export", "starter", "Import CSV files with column mapping and duplicate checks; export any list."),
    Feature("standard_reports", "Standard reports", "starter", "Report builder with charts, filters and CSV export."),
    Feature("api_access", "APIs", "starter", "REST API with personal API keys."),
    Feature("mobile_app", "Mobile apps", "starter", "Install AIVA on your phone's home screen."),
    Feature("mass_email", "Email integration & mass emails", "starter", "Send campaigns to filtered lists with open tracking and unsubscribe links."),
    Feature("calling", "Built-in calling", "starter", "Click to call, call timer and one-click call logging."),
    Feature("multiple_pipelines", "Multiple sales pipelines", "starter", "Separate pipelines and stages for each sales motion."),
    Feature("calendar_sync", "Calendar integration", "starter", "Subscribe to your AIVA tasks and meetings from Google or Outlook Calendar."),
    Feature("web_forms", "Data capture via forms", "starter", "Hosted and embeddable forms that create leads."),
    Feature("forecasting", "Sales forecasting", "starter", "Quarterly forecast by rep with quotas and attainment."),
    Feature("data_enrichment", "Data enrichment", "starter", "Fill in company, website, seniority and phone formats automatically."),
    Feature("custom_modules", "Custom modules", "starter", "Track anything with your own record types and fields."),
    Feature("hipaa", "HIPAA compliance", "starter", "Enforced two-step sign-in, short sessions and an audit of record views."),
    Feature("mailbox_sync", "Gmail and Outlook integration", "starter", "Send from your own mailbox and pull replies into AIVA."),
    Feature("chat_integrations", "Slack, Zoom, Teams integration", "starter", "Deal and case alerts in Slack or Teams; Zoom links on meetings."),
    Feature("service_desk", "Cases, SLAs and knowledge base", "starter", "Support queues with response targets and a knowledge base."),
    Feature("lead_scoring", "Lead scoring", "starter", "Automatic A–D grades from rules you control."),
    # ---------------------------------------------------------------- Pro
    Feature("ai_agents", "AI agents", "pro", "Agents that qualify leads, coach deals, triage cases and draft follow-ups."),
    Feature("process_management", "Process management", "pro", "Stage playbooks with required fields and checklists."),
    Feature("inventory", "Inventory management", "pro", "Stock levels, receipts, adjustments and low-stock alerts."),
    Feature("predictive_intelligence", "Predictive intelligence", "pro", "Win likelihood and risk for every open deal, learned from your history."),
    Feature("unlimited_reports", "Unlimited reports", "pro", "No limit on saved reports and dashboards."),
    Feature("cpq", "Configure, Price, Quote (CPQ)", "pro", "Product catalog, quote builder, discounts and tax."),
    Feature("custom_portals", "Custom portals", "pro", "Branded portals for partners with deal registration."),
    Feature("customer_journeys", "Customer journeys", "pro", "Multi-step nurture journeys with emails, waits and tasks."),
    Feature("web_to_case", "Web-to-case forms", "pro", "Forms that open support cases."),
    Feature("customer_portal", "Customer portals", "pro", "Customers sign in to raise cases, read articles and accept quotes."),
    Feature("google_ads", "Google Ads integration", "pro", "Capture Google Ads leads and export offline conversions."),
    # ---------------------------------------------------------------- Enterprise
    Feature("multi_team", "Multi-team management", "enterprise", "Nested teams, people on several teams and team-based record visibility."),
    Feature("reporting_hierarchy", "Reporting hierarchy", "enterprise", "Who-reports-to-whom, with managers seeing their reports' records."),
    Feature("custom_buttons", "Custom buttons and functions", "enterprise", "Buttons on records that call webhooks, open links or run functions."),
    Feature("extended_fields", "Extended field types", "enterprise", "Currency, percent, multi-select, lookup, formula and more."),
    Feature("sequential_forms", "Sequential data collection", "enterprise", "Step-by-step forms and record wizards."),
    Feature("approvals", "Approval process", "enterprise", "Approval rules for deals and quotes with an approvals inbox."),
    Feature("abm", "Account Based Marketing", "enterprise", "Target account tiers, engagement scores and buying-committee coverage."),
    Feature("sandbox", "Developer sandbox", "enterprise", "A copy of your setup to build and test changes, then deploy them."),
    Feature("field_encryption", "Field-level encryption", "enterprise", "Encrypt sensitive custom fields at rest and mask them by role."),
    Feature("extended_ai", "Extended AI capabilities", "enterprise", "Ask AIVA questions in plain English, AI summaries and reply drafts."),
    Feature("quickbooks", "QuickBooks integration", "enterprise", "Invoices from quotes, exported in QuickBooks Online format."),
    Feature("sso", "Single sign-on", "enterprise", "Sign in through your own identity provider (Okta, Microsoft Entra ID, Google Workspace and other OpenID Connect providers)."),
)

FEATURE_BY_KEY: dict[str, Feature] = {feature.key: feature for feature in FEATURES}
ALL_FEATURES: frozenset[str] = frozenset(FEATURE_BY_KEY)


def _features_up_to(plan: str) -> frozenset[str]:
    rank = PAID_PLANS.index(plan)
    return frozenset(f.key for f in FEATURES if PAID_PLANS.index(f.plan) <= rank)


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price: Decimal  # per user per month
    tagline: str
    features: frozenset[str]
    limits: dict[str, int | None] = field(default_factory=dict)
    highlight: bool = False

    def limit(self, key: str) -> int | None:
        return self.limits.get(key)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "price": float(self.price),
            "tagline": self.tagline,
            "features": sorted(self.features),
            "limits": self.limits,
            "highlight": self.highlight,
        }


UNLIMITED: dict[str, int | None] = {}

PLANS: dict[str, Plan] = {
    "trial": Plan(
        "trial",
        "Free trial",
        Decimal("0"),
        f"Everything in Enterprise, free for {TRIAL_DAYS} days",
        ALL_FEATURES,
        UNLIMITED,
    ),
    "starter": Plan(
        "starter",
        "Starter",
        Decimal("19"),
        "Small team, getting started",
        _features_up_to("starter"),
        {"custom_reports": 25, "dashboards": 5, "custom_modules": 3, "web_forms": 5, "pipelines": 5},
    ),
    "pro": Plan(
        "pro",
        "Pro",
        Decimal("29"),
        "Growing team, needs automation",
        _features_up_to("pro"),
        {"custom_modules": 20},
        highlight=True,
    ),
    "enterprise": Plan(
        "enterprise",
        "Enterprise",
        Decimal("59"),
        "Running a mature sales org",
        _features_up_to("enterprise"),
        UNLIMITED,
    ),
}

LIMIT_LABELS = {
    "custom_reports": "saved reports",
    "dashboards": "dashboards",
    "custom_modules": "custom modules",
    "web_forms": "web forms",
    "pipelines": "pipelines",
}


def plan_for(key: str | None) -> Plan:
    return PLANS.get(key or "", PLANS["trial"])


def required_plan(feature: str) -> Plan:
    definition = FEATURE_BY_KEY.get(feature)
    return PLANS[definition.plan] if definition else PLANS["enterprise"]


def catalog() -> dict:
    """Public description of plans for the pricing page."""

    groups = []
    for plan_key in PAID_PLANS:
        groups.append(
            {
                "plan": plan_key,
                "features": [
                    {"key": f.key, "label": f.label, "description": f.description}
                    for f in FEATURES
                    if f.plan == plan_key
                ],
            }
        )
    return {
        "trial_days": TRIAL_DAYS,
        "currency": "USD",
        "billing_period": "per user / month",
        "plans": [PLANS[key].to_dict() for key in PLAN_ORDER],
        "feature_groups": groups,
        "limit_labels": LIMIT_LABELS,
    }
