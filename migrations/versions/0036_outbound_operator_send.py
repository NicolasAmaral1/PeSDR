"""outbound_messages: client_message_id + allow triggered_by='operator'.

Revision ID: 0036_outbound_operator_send
Revises: 0035_talks_assigned_operator
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_outbound_operator_send"
down_revision = "0035_talks_assigned_operator"
branch_labels = None
depends_on = None

_OLD = "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery')"
_NEW = "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery', 'operator')"
_CK = "ck_outbound_triggered_by"


def upgrade() -> None:
    op.add_column("outbound_messages", sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "ux_outbound_client_message", "outbound_messages",
        ["talkflow_id", "client_message_id"],
        unique=True, postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.drop_constraint(_CK, "outbound_messages", type_="check")
    op.create_check_constraint(_CK, "outbound_messages", _NEW)


def downgrade() -> None:
    op.drop_constraint(_CK, "outbound_messages", type_="check")
    op.create_check_constraint(_CK, "outbound_messages", _OLD)
    op.drop_index("ux_outbound_client_message", "outbound_messages")
    op.drop_column("outbound_messages", "client_message_id")
