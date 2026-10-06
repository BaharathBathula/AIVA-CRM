"""add enterprise account hierarchy and archive fields

Revision ID: 0006_accounts_enterprise
Revises: 0005_tasks_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_accounts_enterprise"
down_revision: str | None = "0005_tasks_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "parent_account_id",
            sa.Uuid(),
            nullable=True,
        ),
    )

    op.add_column(
        "accounts",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "accounts",
        sa.Column(
            "archived_at",
            sa.DateTime(
                timezone=True
            ),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_accounts_parent_account_id_accounts",
        "accounts",
        "accounts",
        [
            "parent_account_id"
        ],
        [
            "id"
        ],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_accounts_parent_account_id",
        "accounts",
        [
            "parent_account_id"
        ],
        unique=False,
    )

    op.create_index(
        "ix_accounts_is_archived",
        "accounts",
        [
            "is_archived"
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_accounts_is_archived",
        table_name="accounts",
    )

    op.drop_index(
        "ix_accounts_parent_account_id",
        table_name="accounts",
    )

    op.drop_constraint(
        "fk_accounts_parent_account_id_accounts",
        "accounts",
        type_="foreignkey",
    )

    op.drop_column(
        "accounts",
        "archived_at",
    )

    op.drop_column(
        "accounts",
        "is_archived",
    )

    op.drop_column(
        "accounts",
        "parent_account_id",
    )