"""ORM pra inbound_form_submissions (criada na migration 0030).

Espelha pattern de InboundMessageRow (messaging — Plano 5).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class InboundFormSubmission(Base):
    """Submissão de formulário recebida via webhook.

    Audit + dedup. RLS via tenant_id (FORCE).
    """

    __tablename__ = "inbound_form_submissions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)

    lead_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )

    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String, nullable=False, server_default="queued"
    )
    error_detail: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processed', 'skipped_dedupe', 'error')",
            name="ck_inbound_form_status",
        ),
        Index(
            "uq_inbound_form_extid",
            "tenant_id",
            "provider",
            "external_id",
            unique=True,
        ),
        # Partial index pra worker scan
        Index(
            "ix_inbound_form_lead_status",
            "lead_id",
            "status",
            postgresql_where="status IN ('queued', 'error')",
        ),
    )
