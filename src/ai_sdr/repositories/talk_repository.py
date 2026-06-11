"""TalkRepository — active Talk lookup + creation helpers.

FE-01a ships:
  - find_active_for_lead (worker preprocessing per spec §4 step 3)
  - create (new inbound from Lead with no active Talk)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.talk import Talk

ACTIVE_STATUSES = {"active", "paused", "requires_review"}


class TalkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_active_for_lead(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> Talk | None:
        """Return the open Talk for this Lead, if any.

        V1 invariant: at most one active Talk per (tenant, lead).
        """
        stmt = (
            select(Talk)
            .where(
                Talk.tenant_id == tenant_id,
                Talk.lead_id == lead_id,
                Talk.status.in_(tuple(ACTIVE_STATUSES)),
            )
            .order_by(Talk.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_most_recent_closed(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> Talk | None:
        """Return the most recently closed Talk for this (tenant, lead), or None.

        A Talk is 'closed' when status starts with 'closed_'. Used by
        preprocessing for re-engagement logging (FE-03b §5.5).
        """
        stmt = (
            select(Talk)
            .where(
                Talk.tenant_id == tenant_id,
                Talk.lead_id == lead_id,
                Talk.status.like("closed_%"),
            )
            .order_by(Talk.closed_at.desc().nulls_last())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        treeflow_id: str,
        treeflow_version_id: uuid.UUID,
    ) -> Talk:
        """Create a new active Talk bound to a TreeFlow version snapshot."""
        now = datetime.now(timezone.utc)
        talk = Talk(
            tenant_id=tenant_id,
            lead_id=lead_id,
            treeflow_id=treeflow_id,
            treeflow_version_id=treeflow_version_id,
            status="active",
            handling_mode="ai",
            last_message_at=now,
        )
        self._session.add(talk)
        return talk
