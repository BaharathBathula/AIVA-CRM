"""tasks foundation

Revision ID: 0005_tasks_foundation
Revises: 0004_engagement_foundation
Create Date: 2026-10-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_tasks_foundation"

down_revision: Union[
    str,
    Sequence[str],
    None,
] = "0004_engagement_foundation"

branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None

depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "account_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "opportunity_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "assigned_to_user_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "parent_task_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "title",
            sa.String(length=300),
            nullable=False,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "task_type",
            sa.String(length=50),
            server_default="general",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="open",
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(length=20),
            server_default="medium",
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=30),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "due_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "reminder_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_recurring",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "recurrence_rule",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "is_ai_generated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "external_id",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            server_default=sa.text(
                "'[]'::jsonb"
            ),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text(
                "'{}'::jsonb"
            ),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["leads.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"],
            ["opportunities.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_task_id"],
            ["tasks.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_tasks_account_id"),
        "tasks",
        ["account_id"],
    )

    op.create_index(
        op.f("ix_tasks_contact_id"),
        "tasks",
        ["contact_id"],
    )

    op.create_index(
        op.f("ix_tasks_lead_id"),
        "tasks",
        ["lead_id"],
    )

    op.create_index(
        op.f("ix_tasks_opportunity_id"),
        "tasks",
        ["opportunity_id"],
    )

    op.create_index(
        op.f("ix_tasks_assigned_to_user_id"),
        "tasks",
        ["assigned_to_user_id"],
    )

    op.create_index(
        op.f("ix_tasks_created_by_user_id"),
        "tasks",
        ["created_by_user_id"],
    )

    op.create_index(
        op.f("ix_tasks_parent_task_id"),
        "tasks",
        ["parent_task_id"],
    )

    op.create_index(
        op.f("ix_tasks_task_type"),
        "tasks",
        ["task_type"],
    )

    op.create_index(
        op.f("ix_tasks_status"),
        "tasks",
        ["status"],
    )

    op.create_index(
        op.f("ix_tasks_priority"),
        "tasks",
        ["priority"],
    )

    op.create_index(
        op.f("ix_tasks_source"),
        "tasks",
        ["source"],
    )

    op.create_index(
        op.f("ix_tasks_due_at"),
        "tasks",
        ["due_at"],
    )

    op.create_index(
        op.f("ix_tasks_reminder_at"),
        "tasks",
        ["reminder_at"],
    )

    op.create_index(
        op.f("ix_tasks_is_ai_generated"),
        "tasks",
        ["is_ai_generated"],
    )

    op.create_index(
        op.f("ix_tasks_external_id"),
        "tasks",
        ["external_id"],
    )

    op.create_index(
        op.f("ix_tasks_organization_id"),
        "tasks",
        ["organization_id"],
    )

    op.create_index(
        "ix_tasks_org_status_due",
        "tasks",
        [
            "organization_id",
            "status",
            "due_at",
        ],
    )

    op.create_index(
        "ix_tasks_org_assignee_due",
        "tasks",
        [
            "organization_id",
            "assigned_to_user_id",
            "due_at",
        ],
    )

    op.create_index(
        "ix_tasks_org_priority_status",
        "tasks",
        [
            "organization_id",
            "priority",
            "status",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tasks_org_priority_status",
        table_name="tasks",
    )

    op.drop_index(
        "ix_tasks_org_assignee_due",
        table_name="tasks",
    )

    op.drop_index(
        "ix_tasks_org_status_due",
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_organization_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_external_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_is_ai_generated"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_reminder_at"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_due_at"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_source"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_priority"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_status"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_task_type"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_parent_task_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_created_by_user_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_assigned_to_user_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_opportunity_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_lead_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_contact_id"),
        table_name="tasks",
    )

    op.drop_index(
        op.f("ix_tasks_account_id"),
        table_name="tasks",
    )

    op.drop_table("tasks")
