"""Lead — a person the agent talks to (or hasn't yet).

A lead is per-tenant (RLS-enforced). It carries an optional `whatsapp_e164`
(unique-per-tenant when set), an optional `external_label` (used by the
simulate CLI's --lead flag and any other dev/admin tooling that wants a
human-readable handle), and a status that gates the worker's behavior:

  - 'pending_assignment' — new lead from inbound; messages queue but no step()
    runs until an operator assigns a treeflow via CLI/REST.
  - 'active' — has an attached talkflow; worker drains inbox via runtime.step().
  - 'unreachable' — provider returned RecipientUnreachable; new inbounds get
    skipped (status_skipped) rather than driving step().
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

LeadStatus = Literal["pending_assignment", "active", "unreachable"]


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
