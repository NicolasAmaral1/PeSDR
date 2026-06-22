"""extend outbound_messages.message_type to allow 'audio' (FE-05 voice I/O)

Drops and recreates ck_outbound_message_type and ck_outbound_body_consistency
to accept message_type = 'audio'.  Audio rows keep body_text (transcript)
and have template_ref IS NULL — same shape as text rows.

Revision ID: 0030_extend_outbound_message_type_audio
Revises: 0029_leads_inbound_channel_label
Create Date: 2026-06-20 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_extend_outbound_message_type_audio"
down_revision = "0029_leads_inbound_channel_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old constraints that only allow 'text' / 'template'
    op.drop_constraint("ck_outbound_message_type", "outbound_messages", type_="check")
    op.drop_constraint(
        "ck_outbound_body_consistency", "outbound_messages", type_="check"
    )

    # Recreate extended variants
    op.create_check_constraint(
        "ck_outbound_message_type",
        "outbound_messages",
        "message_type IN ('text', 'template', 'audio')",
    )
    op.create_check_constraint(
        "ck_outbound_body_consistency",
        "outbound_messages",
        "(message_type = 'text' AND body_text IS NOT NULL AND template_ref IS NULL) "
        "OR (message_type = 'template' AND template_ref IS NOT NULL AND body_text IS NULL) "
        "OR (message_type = 'audio' AND body_text IS NOT NULL AND template_ref IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_outbound_message_type", "outbound_messages", type_="check")
    op.drop_constraint(
        "ck_outbound_body_consistency", "outbound_messages", type_="check"
    )

    op.create_check_constraint(
        "ck_outbound_message_type",
        "outbound_messages",
        "message_type IN ('text', 'template')",
    )
    op.create_check_constraint(
        "ck_outbound_body_consistency",
        "outbound_messages",
        "(message_type = 'text' AND body_text IS NOT NULL AND template_ref IS NULL) "
        "OR (message_type = 'template' AND template_ref IS NOT NULL AND body_text IS NULL)",
    )
