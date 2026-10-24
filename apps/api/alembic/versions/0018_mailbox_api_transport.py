"""mailbox api transport

Mailboxes connected through the Gmail or Outlook Mail integration send and sync
through the provider's API instead of SMTP/IMAP app passwords.

Revision ID: 0018_mailbox_api_transport
Revises: 0017_integrations_hub
Create Date: 2026-10-23 23:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_mailbox_api_transport"

down_revision: Union[str, Sequence[str], None] = "0017_integrations_hub"

branch_labels: Union[str, Sequence[str], None] = None

depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mailbox_connections",
        sa.Column("transport", sa.String(length=20), server_default="smtp", nullable=False),
    )
    # Calendar sync looks meetings up by their calendar event and retries pending pushes.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_meetings_calendar_event "
        "ON meetings (organization_id, ((metadata -> 'calendar' ->> 'event_id'))) "
        "WHERE metadata ? 'calendar'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_meetings_calendar_pending "
        "ON meetings (((metadata -> 'calendar' ->> 'state'))) "
        "WHERE (metadata -> 'calendar' ->> 'state') = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_meetings_calendar_pending")
    op.execute("DROP INDEX IF EXISTS ix_meetings_calendar_event")
    op.drop_column("mailbox_connections", "transport")
