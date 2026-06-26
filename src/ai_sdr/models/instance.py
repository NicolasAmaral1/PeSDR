# src/ai_sdr/models/instance.py
"""Instance — a (tenant + channel) operating line. The inbox scope + WS channel key.

An instance = one messaging channel of a tenant (today one WhatsApp number,
keyed by Lead.inbound_channel_label). Funnel is NOT part of the instance —
it's an orthogonal filter on Talk.treeflow_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (UniqueConstraint("tenant_id", "channel_label", name="uq_instances_tenant_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel_label: Mapped[str] = mapped_column(Text(), nullable=False, server_default="main")
    phone_e164: Mapped[str | None] = mapped_column(Text(), nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
