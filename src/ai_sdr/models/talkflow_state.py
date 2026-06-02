"""TalkFlowState — runtime state per Talk (1:1).

The dynamic, mutable state of an in-flight Talk: where in the TreeFlow we
are (current_node), what's been collected, the rolling message window,
any active objection treatment, and the history of handled objections.

JSONB columns hold structured payloads validated by the Pydantic shapes
in ai_sdr.flowengine.state (loaded at runtime, not enforced at the DB).
Trade-off: schema flexibility for runtime-only validation. Acceptable
because the only writer is the FlowEngine itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class TalkFlowState(Base):
    __tablename__ = "talkflow_states"

    talk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("talks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    current_node: Mapped[str] = mapped_column(Text(), nullable=False)

    collected: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    extracted_facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )

    history_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    history_summary_covers_until_turn: Mapped[int | None] = mapped_column(
        Integer(), nullable=True
    )

    active_treatment: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(), nullable=True
    )
    objections_handled: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )
    talkflow_stack: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
