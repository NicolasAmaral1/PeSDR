"""dispatch_actions — entrypoint from post_processing (FE-03c §6.1)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.actions.templating import (
    TemplateRenderError,
    build_template_context,
    render_params,
)
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository

logger = logging.getLogger(__name__)


def _hash_value(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def dispatch_actions(
    *,
    session: AsyncSession,
    repo: ActionExecutionRepository,
    enqueue: Callable[[str], Awaitable[None]],
    state: Any,
    decision: Any,
    node_spec: Any,
    talk: Any,
    lead: Any,
) -> None:
    """For each `node.on_collected` whose field appears in TurnDecision,
    insert a pending action_executions row + enqueue the arq job.

    No-op if `node.on_collected` is empty. Template render failures and
    UNIQUE collisions log and skip (don't raise).
    """
    on_collected_list = list(getattr(node_spec, "on_collected", []) or [])
    if not on_collected_list:
        return

    collected_fields = getattr(decision, "collected_fields", {}) or {}
    context = build_template_context(state, decision, lead, talk)

    for action_spec in on_collected_list:
        if action_spec.field not in collected_fields:
            continue

        try:
            params_resolved = render_params(action_spec.params, context)
        except TemplateRenderError as exc:
            logger.warning(
                "action.dispatch.template_render_failed talk=%s field=%s err=%s",
                getattr(talk, "id", "?"),
                action_spec.field,
                exc,
            )
            continue

        value = collected_fields[action_spec.field]
        value_hash = _hash_value(value)

        execution_id = await repo.insert_pending(
            tenant_id=talk.tenant_id,
            talk_id=talk.id,
            node_id=node_spec.id,
            field=action_spec.field,
            value_hash=value_hash,
            adapter_name=action_spec.adapter,
            handler=action_spec.handler,
            params_resolved=params_resolved,
        )
        if execution_id is None:
            logger.info(
                "action.dispatch.skipped_duplicate talk=%s field=%s value_hash=%s",
                talk.id,
                action_spec.field,
                value_hash,
            )
            continue

        await enqueue(str(execution_id))
        logger.info(
            "action.enqueued execution=%s adapter=%s handler=%s",
            execution_id,
            action_spec.adapter,
            action_spec.handler,
        )
