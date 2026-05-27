"""Shared scheduler helpers — used by process_lead_inbox and _fire_follow_up."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.follow_up.duration import parse_duration
from ai_sdr.models.follow_up_job import FollowUpJob

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant
    from ai_sdr.schemas.treeflow_yaml import FollowUpConfig


async def cancel_pending_for_lead(
    session: AsyncSession,
    lead_id: uuid.UUID,
    *,
    reason: str,
) -> int:
    """Mark all pending follow_up_jobs for this lead as cancelled.

    Returns the number of rows affected. Caller commits.
    """
    result = await session.execute(
        update(FollowUpJob)
        .where(FollowUpJob.lead_id == lead_id, FollowUpJob.status == "pending")
        .values(status="cancelled", error_detail=reason)
    )
    return result.rowcount or 0


async def schedule_next_followup(
    session: AsyncSession,
    talkflow: "TalkFlow",
    lead: "Lead",
    tenant: "Tenant",
    follow_up_config: "FollowUpConfig",
    *,
    next_attempt_number: int,
) -> FollowUpJob:
    """Insert one follow_up_jobs row at now() + sequence[next-1].after.

    `next_attempt_number` is 1-based (1 = first follow-up, 2 = second, ...).
    Caller commits.
    """
    step = follow_up_config.sequence[next_attempt_number - 1]
    delta = parse_duration(step.after)
    scheduled_at = datetime.now(UTC) + delta
    job = FollowUpJob(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        attempt_number=next_attempt_number,
        scheduled_at=scheduled_at,
        status="pending",
    )
    session.add(job)
    await session.flush()
    return job


def mark_cold_if_exhausted(
    talkflow: "TalkFlow",
    follow_up_config: "FollowUpConfig",
    last_attempt_number: int,
) -> bool:
    """Pure helper. If `last_attempt_number >= max_attempts`, sets
    `talkflow.status = 'cold'` and returns True. Otherwise no-op + False.
    """
    if last_attempt_number >= follow_up_config.max_attempts:
        talkflow.status = "cold"
        return True
    return False
