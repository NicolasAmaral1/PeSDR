"""DB ops for action_executions (FE-03c §3.1).

insert_pending uses ON CONFLICT DO NOTHING to enforce idempotency:
re-emitting the same (talk_id, field, value_hash) tuple returns None and
the dispatcher skips enqueue (logs `action.dispatch.skipped_duplicate`).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.action_execution import ActionExecution


class ActionExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_pending(
        self,
        *,
        tenant_id: uuid.UUID,
        talk_id: uuid.UUID,
        node_id: str,
        field: str,
        value_hash: str,
        adapter_name: str,
        handler: str,
        params_resolved: dict[str, Any],
    ) -> uuid.UUID | None:
        """INSERT ... ON CONFLICT DO NOTHING. Returns new id, or None on duplicate."""
        stmt = (
            pg_insert(ActionExecution)
            .values(
                tenant_id=tenant_id,
                talk_id=talk_id,
                node_id=node_id,
                field=field,
                value_hash=value_hash,
                adapter_name=adapter_name,
                handler=handler,
                params_resolved=params_resolved,
                status="pending",
            )
            .on_conflict_do_nothing(constraint="uq_action_executions_dedup")
            .returning(ActionExecution.id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return row.id if row is not None else None

    async def mark_executing(self, execution_id: uuid.UUID) -> ActionExecution | None:
        """SELECT FOR UPDATE + status='executing' + attempts+1. Returns row or None."""
        stmt = select(ActionExecution).where(ActionExecution.id == execution_id).with_for_update()
        result = await self._session.execute(stmt)
        execution = result.scalar_one_or_none()
        if execution is None:
            return None
        execution.status = "executing"
        execution.attempts = (execution.attempts or 0) + 1
        return execution

    async def mark_success(self, execution: ActionExecution, *, external_id: str | None) -> None:
        execution.status = "success"
        execution.external_id = external_id

    async def mark_failed(self, execution: ActionExecution, *, error: str, terminal: bool) -> None:
        execution.last_error = (error or "")[:1000]
        if terminal:
            execution.status = "failed"
