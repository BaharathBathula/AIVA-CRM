"""Permission catalog and built-in roles.

Permissions are plain string keys. Built-in (system) roles are defined in
code so every organization gets the same baseline; organizations can add
custom roles stored in the ``roles`` table with any subset of the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    key: str
    label: str
    group: str
    description: str


PERMISSIONS: tuple[PermissionDef, ...] = (
    # Records (leads, accounts, contacts, opportunities, tasks, activities)
    PermissionDef("records.read", "View records", "Records", "View leads, accounts, contacts, opportunities, tasks and activity."),
    PermissionDef("records.write", "Create and edit records", "Records", "Create and update CRM records."),
    PermissionDef("records.delete", "Delete records", "Records", "Delete or archive CRM records."),
    PermissionDef("records.export", "Export data", "Records", "Export record lists to CSV."),
    PermissionDef("records.view_all", "View all records", "Records", "See records owned by anyone, not only your own team."),
    # Sales
    PermissionDef("sales.products", "Manage products", "Sales", "Create and edit the product catalog and prices."),
    PermissionDef("sales.quotes", "Create quotes", "Sales", "Create, send and update quotes."),
    PermissionDef("sales.quotes_approve", "Approve quotes", "Sales", "Accept or decline quotes and approve discounts."),
    PermissionDef("sales.forecast_view", "View forecasts", "Sales", "See the forecast and quota attainment."),
    PermissionDef("sales.forecast_manage", "Manage quotas", "Sales", "Set quotas for users."),
    PermissionDef("sales.lead_scoring", "Configure lead scoring", "Sales", "Edit lead scoring rules and recalculate scores."),
    # Service
    PermissionDef("service.cases", "Work cases", "Service", "View, create and update support cases."),
    PermissionDef("service.knowledge_write", "Write knowledge articles", "Service", "Create and edit knowledge base drafts."),
    PermissionDef("service.knowledge_publish", "Publish knowledge articles", "Service", "Publish and archive knowledge articles."),
    PermissionDef("service.sla_manage", "Manage SLA policies", "Service", "Create and edit service level agreements."),
    # Analytics
    PermissionDef("analytics.reports", "Create reports", "Analytics", "Build, run and save reports."),
    PermissionDef("analytics.dashboards", "Manage dashboards", "Analytics", "Create and edit dashboards."),
    # Automation
    PermissionDef("automation.workflows", "Manage workflows", "Automation", "Create, edit and turn workflow rules on and off."),
    # Administration
    PermissionDef("admin.users", "Manage users", "Administration", "Invite, deactivate and change roles of users."),
    PermissionDef("admin.roles", "Manage roles", "Administration", "Create custom roles and edit their permissions."),
    PermissionDef("admin.teams", "Manage teams", "Administration", "Create teams and assign members."),
    PermissionDef("admin.audit", "View audit log", "Administration", "See the history of changes and sign-ins."),
    PermissionDef("admin.settings", "Manage organization settings", "Administration", "Change workspace name, currency and security policy."),
)

ALL_PERMISSIONS: frozenset[str] = frozenset(p.key for p in PERMISSIONS)


def _all_except(*excluded: str) -> frozenset[str]:
    return frozenset(ALL_PERMISSIONS - set(excluded))


@dataclass(frozen=True)
class SystemRole:
    key: str
    name: str
    description: str
    permissions: frozenset[str]


SYSTEM_ROLES: dict[str, SystemRole] = {
    role.key: role
    for role in (
        SystemRole("owner", "Owner", "Full access to everything, including billing and ownership transfer.", ALL_PERMISSIONS),
        SystemRole("admin", "Administrator", "Full access to data and settings.", ALL_PERMISSIONS),
        SystemRole(
            "manager",
            "Sales manager",
            "Runs a team: sees all records, sets quotas, approves quotes and builds reports.",
            _all_except("admin.users", "admin.roles", "admin.settings", "admin.audit"),
        ),
        SystemRole(
            "member",
            "Sales representative",
            "Works leads, deals, quotes and tasks.",
            frozenset({
                "records.read", "records.write", "records.export", "records.view_all",
                "sales.quotes", "sales.forecast_view",
                "service.cases", "analytics.reports", "analytics.dashboards",
            }),
        ),
        SystemRole(
            "support_agent",
            "Support agent",
            "Works the service desk and writes knowledge articles.",
            frozenset({
                "records.read", "records.write", "records.view_all",
                "service.cases", "service.knowledge_write", "analytics.reports",
            }),
        ),
        SystemRole(
            "viewer",
            "Read only",
            "Can view records, reports and dashboards but not change anything.",
            frozenset({"records.read", "records.view_all", "sales.forecast_view", "service.cases"}),
        ),
    )
}


def permission_catalog() -> list[dict]:
    return [
        {"key": p.key, "label": p.label, "group": p.group, "description": p.description}
        for p in PERMISSIONS
    ]


def normalize_permissions(values: list[str] | set[str] | None) -> list[str]:
    return sorted({value for value in (values or []) if value in ALL_PERMISSIONS})
