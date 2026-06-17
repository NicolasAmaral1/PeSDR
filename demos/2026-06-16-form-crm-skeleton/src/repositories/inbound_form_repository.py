"""Repository pra InboundFormSubmission.

Padroniza ops de DB sobre inbound_form_submissions. Mesmo pattern de
`inbound_message_repository.py` (Plano 5).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.inbound_form_submission import InboundFormSubmission


class InboundFormRepository:
    """Operações de DB sobre inbound_form_submissions.

    Convenções:
    - Caller é responsável por commit (repo não chama session.commit())
    - Caller é responsável por set_tenant_context() pra queries RLS
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_pending(
        self,
        *,
        tenant_id: UUID,
        provider: str,
        external_id: str,
        lead_id: UUID | None,
        raw: dict[str, Any],
        field_values: dict[str, Any],
        submitted_at: datetime,
    ) -> UUID | None:
        """INSERT ... ON CONFLICT DO NOTHING.

        Returns:
            UUID da nova row OU None se conflict (dedup).
        """
        # TODO: implementação real
        # stmt = (
        #     pg_insert(InboundFormSubmission)
        #     .values(
        #         tenant_id=tenant_id,
        #         provider=provider,
        #         external_id=external_id,
        #         lead_id=lead_id,
        #         raw=raw,
        #         field_values=field_values,
        #         submitted_at=submitted_at,
        #         status="queued",
        #     )
        #     .on_conflict_do_nothing(
        #         index_elements=["tenant_id", "provider", "external_id"]
        #     )
        #     .returning(InboundFormSubmission.id)
        # )
        # result = await self.session.execute(stmt)
        # return result.scalar_one_or_none()
        raise NotImplementedError("Fase A T3 — insert_pending")

    async def get_by_id(self, submission_id: UUID) -> InboundFormSubmission | None:
        """SELECT * WHERE id = ?"""
        raise NotImplementedError("Fase A T3 — get_by_id")

    async def mark_processed(self, submission_id: UUID) -> None:
        """UPDATE status='processed', processed_at=now() WHERE id = ?"""
        raise NotImplementedError("Fase A T3 — mark_processed")

    async def mark_error(self, submission_id: UUID, detail: str) -> None:
        """UPDATE status='error', error_detail=?, processed_at=now() WHERE id = ?"""
        raise NotImplementedError("Fase A T3 — mark_error")
