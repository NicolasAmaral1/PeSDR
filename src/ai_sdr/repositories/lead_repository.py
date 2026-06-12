"""LeadRepository — thin async helpers around Lead.

FE-01a ships the minimum FE-01b needs:
  - find_by_channel_identifier (worker resolves Lead from inbound payload)
  - set_risk_level (Sentinel transitions risk_level + reason atomically)

Heavier queries (lead listing, search) belong to FE-06 API surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_channel_identifier(
        self,
        tenant_id: uuid.UUID,
        channel: str,
        identifier: str,
    ) -> Lead | None:
        """Look up a Lead by a single channel identifier.

        Operates under the caller's tenant context (RLS); the explicit
        tenant_id filter is belt-and-suspenders.
        """
        stmt = (
            select(Lead)
            .where(
                Lead.tenant_id == tenant_id,
                text("leads.channel_identifiers ->> :ch = :v"),
            )
            .params(ch=channel, v=identifier)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_risk_level(
        self,
        lead: Lead,
        level: str,
        reason: str | None = None,
    ) -> None:
        """Transition a Lead's risk_level + record reason + timestamp."""
        if level not in ("normal", "elevated", "banned"):
            raise ValueError(f"invalid risk_level: {level!r}")
        lead.risk_level = level
        lead.risk_level_reason = reason
        lead.risk_level_since = datetime.now(timezone.utc)
