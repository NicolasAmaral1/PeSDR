"""ActionExecution — ORM for action_executions table (FE-03c §5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base
from ai_sdr.models.action_status import ALL_STATUSES, ActionStatus


class ActionExecution(Base):
    __tablename__ = "action_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    talk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("talks.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(Text(), nullable=False)
    field: Mapped[str] = mapped_column(Text(), nullable=False)
    value_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    adapter_name: Mapped[str] = mapped_column(Text(), nullable=False)
    handler: Mapped[str] = mapped_column(Text(), nullable=False)
    params_resolved: Mapped[dict[str, object]] = mapped_column(JSONB(), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Text(), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer(), nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
            name="ck_action_executions_status",
        ),
        UniqueConstraint("talk_id", "field", "value_hash", name="uq_action_executions_dedup"),
        Index(
            "ix_action_executions_pending",
            "status",
            "created_at",
            postgresql_where=text("status IN ('pending', 'executing')"),
        ),
        Index("ix_action_executions_tenant_talk", "tenant_id", "talk_id"),
    )
