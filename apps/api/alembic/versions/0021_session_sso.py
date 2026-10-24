"""sessions remember whether they came through single sign-on

A session can be limited to one workspace for two reasons: it was started
through that workspace's single sign-on, or another of the person's workspaces
requires single sign-on (so a password session must not switch into it). Only
the first kind is exempt from "Require single sign-on"; the flag tells them apart.

Revision ID: 0021_session_sso
Revises: 0020_integration_records
Create Date: 2026-10-24 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0021_session_sso'

down_revision: Union[str, Sequence[str], None] = '0020_integration_records'

branch_labels: Union[str, Sequence[str], None] = None

depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_sessions', sa.Column('via_sso', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    # Before this revision only single sign-on bound a session to a workspace.
    op.execute("UPDATE user_sessions SET via_sso = true WHERE bound_organization_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column('user_sessions', 'via_sso')
