"""CRM foundation

Revision ID: 0002_crm_foundation
Revises: 0001_tenant_foundation
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_crm_foundation"

down_revision: Union[str, Sequence[str], None] = (
    "0001_tenant_foundation"
)

branch_labels: Union[str, Sequence[str], None] = None

depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column(
            "name",
            sa.String(length=250),
            nullable=False,
        ),
        sa.Column(
            "domain",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "website",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "industry",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "phone",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "lifecycle_stage",
            sa.String(length=50),
            server_default="prospect",
            nullable=False,
        ),
        sa.Column(
            "employee_count",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "annual_revenue",
            sa.Numeric(18, 2),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "billing_address_line1",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "billing_address_line2",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "billing_city",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column(
            "billing_state",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column(
            "billing_postal_code",
            sa.String(length=30),
            nullable=True,
        ),
        sa.Column(
            "billing_country",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            nullable=True,
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
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_accounts_name"),
        "accounts",
        ["name"],
    )

    op.create_index(
        op.f("ix_accounts_domain"),
        "accounts",
        ["domain"],
    )

    op.create_index(
        op.f("ix_accounts_lifecycle_stage"),
        "accounts",
        ["lifecycle_stage"],
    )

    op.create_index(
        op.f("ix_accounts_owner_user_id"),
        "accounts",
        ["owner_user_id"],
    )

    op.create_index(
        op.f("ix_accounts_organization_id"),
        "accounts",
        ["organization_id"],
    )

    op.create_table(
        "contacts",
        sa.Column(
            "account_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "first_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "last_name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=True,
        ),
        sa.Column(
            "phone",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "mobile",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "job_title",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "department",
            sa.String(length=150),
            nullable=True,
        ),
        sa.Column(
            "linkedin_url",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_contacts_account_id"),
        "contacts",
        ["account_id"],
    )

    op.create_index(
        op.f("ix_contacts_email"),
        "contacts",
        ["email"],
    )

    op.create_index(
        op.f("ix_contacts_organization_id"),
        "contacts",
        ["organization_id"],
    )

    op.create_table(
        "activities",
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
            "created_by_user_id",
            sa.Uuid(),
            nullable=True,
        ),
        sa.Column(
            "activity_type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "subject",
            sa.String(length=300),
            nullable=False,
        ),
        sa.Column(
            "body",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "direction",
            sa.String(length=20),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "external_id",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
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
            ["created_by_user_id"],
            ["users.id"],
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
        op.f("ix_activities_account_id"),
        "activities",
        ["account_id"],
    )

    op.create_index(
        op.f("ix_activities_contact_id"),
        "activities",
        ["contact_id"],
    )

    op.create_index(
        op.f("ix_activities_created_by_user_id"),
        "activities",
        ["created_by_user_id"],
    )

    op.create_index(
        op.f("ix_activities_activity_type"),
        "activities",
        ["activity_type"],
    )

    op.create_index(
        op.f("ix_activities_organization_id"),
        "activities",
        ["organization_id"],
    )

    op.create_index(
        "ix_activities_organization_occurred_at",
        "activities",
        [
            "organization_id",
            "occurred_at",
        ],
    )


def downgrade() -> None:
    op.drop_table("activities")
    op.drop_table("contacts")
    op.drop_table("accounts")
