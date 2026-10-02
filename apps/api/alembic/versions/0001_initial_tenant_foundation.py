"""initial tenant foundation

Revision ID: 0001_tenant_foundation
Revises:
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_tenant_foundation"

down_revision: Union[str, Sequence[str], None] = None

branch_labels: Union[str, Sequence[str], None] = None

depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "slug",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_organizations"),
        ),
    )

    op.create_index(
        op.f("ix_organizations_slug"),
        "organizations",
        ["slug"],
        unique=True,
    )

    op.create_table(
        "users",
        sa.Column(
            "email",
            sa.String(length=320),
            nullable=False,
        ),
        sa.Column(
            "full_name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "password_hash",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_users"),
        ),
    )

    op.create_index(
        op.f("ix_users_email"),
        "users",
        ["email"],
        unique=True,
    )

    op.create_table(
        "organization_memberships",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=50),
            server_default="member",
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
            name=op.f(
                "fk_organization_memberships_organization_id_organizations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f(
                "fk_organization_memberships_user_id_users"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f(
                "pk_organization_memberships"
            ),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_membership_user",
        ),
    )

    op.create_index(
        op.f(
            "ix_organization_memberships_organization_id"
        ),
        "organization_memberships",
        ["organization_id"],
        unique=False,
    )

    op.create_index(
        op.f(
            "ix_organization_memberships_user_id"
        ),
        "organization_memberships",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f(
            "ix_organization_memberships_user_id"
        ),
        table_name="organization_memberships",
    )

    op.drop_index(
        op.f(
            "ix_organization_memberships_organization_id"
        ),
        table_name="organization_memberships",
    )

    op.drop_table(
        "organization_memberships"
    )

    op.drop_index(
        op.f("ix_users_email"),
        table_name="users",
    )

    op.drop_table(
        "users"
    )

    op.drop_index(
        op.f("ix_organizations_slug"),
        table_name="organizations",
    )

    op.drop_table(
        "organizations"
    )
