"""OutboundMessage — audit row for each adapter send (success or failure).

Tenant-scoped (RLS), FK to talkflow + lead, optional FKs to the
inbound_message or follow_up_job that triggered the send. XOR check
constraint at the DB level ensures text rows carry body_text and
template rows carry template_ref.

Worker (Plan 5 + 9 paths) and scanner (Plan 9) insert via the helpers
in ai_sdr.observability.outbound_audit. CLI + future conversation
viewer (P11b) read via standard SELECT under tenant context.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    talkflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("talkflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    message_type: Mapped[str] = mapped_column(Text(), nullable=False)

    body_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_language: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_params: Mapped[list[str] | None] = mapped_column(JSONB(), nullable=True)

    status: Mapped[str] = mapped_column(Text(), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)

    triggered_by: Mapped[str] = mapped_column(Text(), nullable=False)
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbound_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    follow_up_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("follow_up_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    media_type: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="text"
    )
    media_storage_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    synthesis_voice_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    voice_emotion: Mapped[str | None] = mapped_column(Text(), nullable=True)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
