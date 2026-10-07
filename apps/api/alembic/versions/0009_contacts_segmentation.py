"""add contact segmentation and tags

Revision ID: 0009_contacts_segmentation
Revises: 0008_contacts_archive
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_contacts_segmentation"
down_revision = "0008_contacts_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "segment",
            sa.String(
                length=100
            ),
            nullable=True,
        ),
    )

    op.add_column(
        "contacts",
        sa.Column(
            "tags",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=False,
            server_default="[]",
        ),
    )

    op.create_index(
        "ix_contacts_segment",
        "contacts",
        ["segment"],
        unique=False,
    )

    op.create_index(
        "ix_contacts_tags_gin",
        "contacts",
        ["tags"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_contacts_tags_gin",
        table_name="contacts",
    )

    op.drop_index(
        "ix_contacts_segment",
        table_name="contacts",
    )

    op.drop_column(
        "contacts",
        "tags",
    )

    op.drop_column(
        "contacts",
        "segment",
    )