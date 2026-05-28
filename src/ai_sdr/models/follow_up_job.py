"""FollowUpJob — a scheduled or fired follow-up attempt for a lead.

Lifecycle: pending -> completed | cancelled | error. Row stays forever
(audit). Pending rows are scanned every 60s by the follow_up_scanner
cron; once fired (or cancelled), they are terminal.

Schedule-one-at-a-time: each fired job inserts the next attempt's row
(unless max_attempts reached, which marks talkflow.status='cold').
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class FollowUpJob(Base):
    __tablename__ = "follow_up_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    talkflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("talkflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default="pending")
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
