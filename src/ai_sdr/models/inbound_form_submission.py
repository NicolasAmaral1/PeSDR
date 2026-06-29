"""InboundFormSubmission — persistence for inbound form provider submissions.

Analogue of `InboundMessageRow` but for the Form ingestion boundary. Dedup is
enforced by UNIQUE (tenant_id, provider, external_id) — the same submission
arriving twice (Respondi retry, network glitch) is silently ignored.

The `field_values` column is the *normalized* version of `raw` after the
adapter's `field_mapping` has been applied. `raw` is kept verbatim for audit
and replay.

Lifecycle:
  - queued      — webhook persisted, worker hasn't picked up yet
  - processed   — worker created Talk + (optionally) sent HSM proactive
  - skipped_dedupe — duplicate of an earlier submission (never used in code; on
                    conflict we just no-op, so the row never exists. Kept in
                    the CHECK so future scripts that backfill have the option.)
  - error       — terminal failure during worker processing
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class InboundFormSubmission(Base):
    __tablename__ = "inbound_form_submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    external_id: Mapped[str] = mapped_column(Text(), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    field_values: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default="queued")
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
