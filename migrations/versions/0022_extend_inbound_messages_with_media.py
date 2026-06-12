"""extend inbound_messages with voice/media fields (FlowEngine FE-01a)

Per spec §13.4. Existing rows keep defaults (media_type='text').

Revision ID: 0022_extend_inbound_messages_with_media
Revises: 0021_extend_outbound_messages_with_media
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_extend_inbound_messages_with_media"
down_revision = "0021_extend_outbound_messages_with_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbound_messages",
        sa.Column("media_type", sa.Text(), nullable=False, server_default="text"),
    )
    op.add_column(
        "inbound_messages",
        sa.Column("media_storage_key", sa.Text(), nullable=True),
    )
    op.add_column("inbound_messages", sa.Column("audio_url", sa.Text(), nullable=True))
    op.add_column("inbound_messages", sa.Column("transcription", sa.Text(), nullable=True))
    op.add_column(
        "inbound_messages",
        sa.Column("transcription_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "inbound_messages",
        sa.Column("transcription_provider", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_inbound_media_type",
        "inbound_messages",
        "media_type IN ('text', 'audio', 'image', 'video', 'document')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_inbound_media_type", "inbound_messages", type_="check")
    op.drop_column("inbound_messages", "transcription_provider")
    op.drop_column("inbound_messages", "transcription_confidence")
    op.drop_column("inbound_messages", "transcription")
    op.drop_column("inbound_messages", "audio_url")
    op.drop_column("inbound_messages", "media_storage_key")
    op.drop_column("inbound_messages", "media_type")
