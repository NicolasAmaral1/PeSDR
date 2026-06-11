"""Lead — the prospect identity. Long-lived across multiple Talks.

A lead is per-tenant (RLS-enforced). It carries an optional `whatsapp_e164`
(unique-per-tenant when set), an optional `external_label` (used by the
simulate CLI's --lead flag and any other dev/admin tooling that wants a
human-readable handle), and a status that gates the worker's behavior:

  - 'pending_assignment' — new lead from inbound; messages queue but no step()
    runs until an operator assigns a treeflow via CLI/REST.
  - 'active' — has an attached talkflow; worker drains inbox via runtime.step().
  - 'unreachable' — provider returned RecipientUnreachable; new inbounds get
    skipped (status_skipped) rather than driving step().

FlowEngine FE-01a extends Lead with long-lived identity fields:

  - channel_identifiers: routing per channel (e.g. WhatsApp e164, Telegram id)
  - display_name: human-friendly label rendered in console + system prompts
  - profile: long-term memory store (V1 disabled; toggled per Lead)
  - risk_level: Sentinel state machine ('normal' / 'elevated' / 'banned')
  - acquisition_metadata: UTM + source for BI attribution

These fields are populated incrementally as the FlowEngine wires them; the
defaults make the columns safe for existing legacy rows.

IMPORTANT: This Lead is NOT the same as the P11 ``users`` table. P11 ``User``
represents an operator (system user); ``Lead`` represents the prospect
being qualified. Never conflate the two.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

LeadStatus = Literal["pending_assignment", "active", "unreachable"]
RiskLevel = Literal["normal", "elevated", "banned"]


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    whatsapp_e164: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_label: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default="pending_assignment")
    unreachable_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    # FlowEngine identity (added by migration 0012)
    channel_identifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    display_name: Mapped[str | None] = mapped_column(Text(), nullable=True)
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    profile_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    long_term_memory_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=func.cast("false", Boolean())
    )
    risk_level: Mapped[str] = mapped_column(Text(), nullable=False, server_default="normal")
    risk_level_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    risk_level_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    acquisition_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
