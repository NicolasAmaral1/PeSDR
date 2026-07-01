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

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
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

    # Sandbox flag (migration 0032 — PR #24, Nicolas option (a))
    # Carried explicitly on Lead so crons + inbox queries can filter via
    # WHERE is_sandbox = false without JOINing to talks.
    is_sandbox: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=func.cast("false", Boolean())
    )

    # Multi-channel pre-paving (Hedge 1) — added by migration 0029.
    # When a tenant gains multiple messaging channels (e.g., 2 WhatsApp numbers),
    # this records which channel originated the lead. Today all leads default
    # to 'main'; when multi-channel ships, the webhook handler stamps the real
    # channel label here.
    inbound_channel_label: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="main"
    )

    # CRM refs (migration 0033, ADR CRM Fase 1 "write-only + refs").
    # Per-vendor external CRM ids. Shape:
    #   {"rdstation": {"contact_id": "...", "deal_id": "...",
    #                  "last_synced_at": "..."}}
    # Read at template render time so Jinja2 can pull contact_external_id
    # for `create_or_update_deal`. Backends update via UPDATE leads SET
    # crm_refs = jsonb_set(...) after each external write.
    crm_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=text("'{}'::jsonb")
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
