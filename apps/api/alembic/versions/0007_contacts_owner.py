"""add contact owner

Revision ID: 0007_contacts_owner
Revises: 0006_accounts_enterprise
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_contacts_owner"
down_revision = "0006_accounts_enterprise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_contacts_owner_user_id_users",
        "contacts",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_contacts_owner_user_id",
        "contacts",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contacts_owner_user_id",
        table_name="contacts",
    )

    op.drop_constraint(
        "fk_contacts_owner_user_id_users",
        "contacts",
        type_="foreignkey",
    )

    op.drop_column(
        "contacts",
        "owner_user_id",
    )