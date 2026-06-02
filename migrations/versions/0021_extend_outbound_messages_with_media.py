"""extend outbound_messages with voice/media fields (FlowEngine FE-01a)

Per spec §13.4 (VoiceAdapter). Existing rows keep defaults (media_type='text').

Revision ID: 0021_extend_outbound_messages_with_media
Revises: 0020_create_treeflow_improvement_suggestions_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_extend_outbound_messages_with_media"
down_revision = "0020_create_treeflow_improvement_suggestions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbound_messages",
        sa.Column("media_type", sa.Text(), nullable=False, server_default="text"),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("media_storage_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "outbound_messages", sa.Column("audio_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "outbound_messages",
        sa.Column("audio_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("synthesis_voice_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("voice_emotion", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_outbound_media_type",
        "outbound_messages",
        "media_type IN ('text', 'audio', 'image', 'video')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_outbound_media_type", "outbound_messages", type_="check")
    op.drop_column("outbound_messages", "voice_emotion")
    op.drop_column("outbound_messages", "synthesis_voice_id")
    op.drop_column("outbound_messages", "audio_duration_ms")
    op.drop_column("outbound_messages", "audio_url")
    op.drop_column("outbound_messages", "media_storage_key")
    op.drop_column("outbound_messages", "media_type")
