"""execute_action — arq worker job (FE-03c §6.2).

Cross-tenant: bypasses RLS for the action_executions lookup (worker is
trusted, same pattern as scan_talks). Re-sets tenant context before any
tenant-scoped reads (e.g. secrets via SopsLoader, tenant.yaml loader).

State machine:
  pending --enqueue--> executing --success--> success
                          |
                          +-- exception (attempts < 3) --> raise (arq retries)
                          +-- exception (attempts >= 3) --> failed (terminal)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.actions.factory import build_action_adapter
from ai_sdr.models.action_execution import ActionExecution
from ai_sdr.models.tenant import Tenant
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3


async def execute_action(ctx: dict[str, Any], execution_id_str: str) -> None:
    execution_id = UUID(execution_id_str)
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        repo = ActionExecutionRepository(session)
        execution = await repo.mark_executing(execution_id)
        if execution is None:
            logger.info("action.execution_not_found id=%s", execution_id)
            await session.commit()
            return
        await set_tenant_context(session, execution.tenant_id)
        await session.commit()

        try:
            tenant = await _load_tenant_by_id(session, execution.tenant_id)
            adapter = build_action_adapter(execution.adapter_name, tenant)
            result = await adapter.execute(
                handler=execution.handler,
                params=execution.params_resolved,
            )
        except Exception as exc:
            await session.execute(text("SET LOCAL row_security = off"))
            refresh = await _refetch_locked(session, execution_id)
            if refresh is None:
                await session.commit()
                return
            is_terminal = refresh.attempts >= MAX_ATTEMPTS
            await repo.mark_failed(refresh, error=str(exc), terminal=is_terminal)
            await session.commit()
            if is_terminal:
                logger.error(
                    "action.failed execution=%s attempts=%d err=%s",
                    execution_id, refresh.attempts, exc,
                )
                return
            logger.warning(
                "action.retry execution=%s attempts=%d err=%s",
                execution_id, refresh.attempts, exc,
            )
            raise

        await session.execute(text("SET LOCAL row_security = off"))
        refresh = await _refetch_locked(session, execution_id)
        if refresh is None:
            await session.commit()
            return
        await repo.mark_success(refresh, external_id=result.external_id)
        await session.commit()
        logger.info(
            "action.executed execution=%s attempts=%d external_id=%s",
            execution_id, refresh.attempts, result.external_id,
        )


async def _refetch_locked(
    session: AsyncSession, execution_id: UUID
) -> ActionExecution | None:
    return (
        await session.execute(
            select(ActionExecution)
            .where(ActionExecution.id == execution_id)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _load_tenant_by_id(session: AsyncSession, tenant_id: uuid.UUID) -> Any:
    """Map tenant_id (UUID) → TenantConfig via the on-disk tenant.yaml.

    Reuses the existing TenantLoader pattern (load by slug). Wrapper exists so
    tests can patch this symbol without monkey-patching TenantLoader.
    """
    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one()
    loader = TenantLoader(Path(get_settings().tenants_dir))
    return loader.load(tenant_row.slug)
