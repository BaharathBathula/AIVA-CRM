"""Remember the last page support opened, so repeat page views aren't logged every request.

Revision ID: 0016_support_access_path
Revises: 0015_settings_suite
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_support_access_path"
down_revision = "0015_settings_suite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("support_access_grants", sa.Column("last_path", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("support_access_grants", "last_path")
