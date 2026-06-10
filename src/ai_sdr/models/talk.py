"""Talk — a conversation session between agent and Lead.

A Talk is a discrete period of agent-lead interaction. A Lead can have
many Talks over time (V1 restricts to one active per tenant). Each Talk
is bound to an immutable TreeFlow version snapshot for its lifetime.

Lifecycle (status):
  - active                  : pipeline runs, lead is engaged
  - paused                  : reserved (operator pause, V1 unused)
  - requires_review         : escalated to human; handling_mode flips to 'human'
  - closed_completed        : closure rule (success/failure/no_interest) fired
  - closed_inactivity       : exceeded talk_lifecycle.close_after_inactivity
  - closed_optout           : opt-out keyword detected
  - closed_banned           : Sentinel attack verdict

Handling mode controls runtime behavior:
  - ai                      : pipeline generates and sends responses
  - human                   : operator owns the conversation; pipeline only logs
  - auto_with_approval      : pipeline generates; response held in review queue
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base
from ai_sdr.models.review_reason import RequiresReviewReason

TalkStatus = Literal[
    "active",
    "paused",
    "requires_review",
    "closed_completed",
    "closed_inactivity",
    "closed_optout",
    "closed_banned",
]

HandlingMode = Literal["ai", "human", "auto_with_approval"]


class Talk(Base):
    __tablename__ = "talks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    treeflow_id: Mapped[str] = mapped_column(Text(), nullable=False)
    treeflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("treeflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(Text(), nullable=False)
    handling_mode: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="ai"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(Text(), nullable=True)

    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalation_category: Mapped[str | None] = mapped_column(Text(), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    requires_review_reason: Mapped[RequiresReviewReason | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
    )

    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    experiment_variant: Mapped[str | None] = mapped_column(Text(), nullable=True)

    turn_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default="0")
    tokens_consumed: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
