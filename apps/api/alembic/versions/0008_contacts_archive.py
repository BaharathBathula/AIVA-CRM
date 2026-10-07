"""add contact archive support

Revision ID: 0008_contacts_archive
Revises: 0007_contacts_owner
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_contacts_archive"
down_revision = "0007_contacts_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "contacts",
        sa.Column(
            "archived_at",
            sa.DateTime(
                timezone=True
            ),
            nullable=True,
        ),
    )

    op.create_index(
        "ix_contacts_is_archived",
        "contacts",
        ["is_archived"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contacts_is_archived",
        table_name="contacts",
    )

    op.drop_column(
        "contacts",
        "archived_at",
    )

    op.drop_column(
        "contacts",
        "is_archived",
    )