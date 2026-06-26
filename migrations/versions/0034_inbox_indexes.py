# migrations/versions/0034_inbox_indexes.py
"""Indexes for the contact-based inbox read paths.

Revision ID: 0034_inbox_indexes
Revises: 0033_operator_read_markers
"""

from alembic import op

revision = "0034_inbox_indexes"
down_revision = "0033_operator_read_markers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_leads_tenant_channel_created", "leads", ["tenant_id", "inbound_channel_label", "created_at"])
    op.create_index("ix_inbound_lead_received", "inbound_messages", ["lead_id", "received_at"])
    op.create_index("ix_outbound_lead_sent", "outbound_messages", ["lead_id", "sent_at"])
    op.create_index("ix_talks_tenant_lead_status", "talks", ["tenant_id", "lead_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_talks_tenant_lead_status", "talks")
    op.drop_index("ix_outbound_lead_sent", "outbound_messages")
    op.drop_index("ix_inbound_lead_received", "inbound_messages")
    op.drop_index("ix_leads_tenant_channel_created", "leads")
