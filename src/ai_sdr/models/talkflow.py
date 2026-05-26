"""TalkFlow — a live conversation instance traversing a TreeFlow version."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

TalkFlowStatus = Literal["active", "completed", "cold"]


class TalkFlow(Base):
    __tablename__ = "talkflows"
    __table_args__ = (UniqueConstraint("tenant_id", "lead_id", name="uq_talkflows_tenant_lead"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="RESTRICT"),
        nullable=False,
    )
    treeflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("treeflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "completed", "cold", name="talkflow_status"),
        nullable=False,
        server_default="active",
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
