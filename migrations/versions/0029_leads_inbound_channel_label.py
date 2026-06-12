"""leads.inbound_channel_label — multi-channel pre-paving (Hedge 1)

Adds the column that records which messaging channel originated each lead.
All existing rows default to 'main'. When multi-channel ships, the
webhook handler stamps the real channel label here.

Why now: pre-paves the multi-channel-per-tenant architecture without
committing to the full refactor. Backfill cost is zero (default 'main'
applies to existing rows on apply).

Revision ID: 0029_leads_inbound_channel_label
Revises: 0028_action_executions
Create Date: 2026-06-12 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_leads_inbound_channel_label"
down_revision = "0028_action_executions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "inbound_channel_label",
            sa.Text(),
            nullable=False,
            server_default="main",
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "inbound_channel_label")
