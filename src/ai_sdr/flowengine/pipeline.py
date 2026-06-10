"""FlowEngine v2 orchestrator — run_turn composes Tasks 1-16.

Pipeline per spec §4 (12 steps). Owns the per-(tenant, lead) advisory
lock and the surrounding transaction. Returns a RunTurnResult describing
the outcome.

Out of scope for FE-01b (delegated to FE-03+): Sentinel layer, voice
inbound transcription, humanization chunks, event emission, lifecycle
close enforcement. Those slots are commented in the function body so the
ordering stays honest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import Runnable
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.advisory_lock import acquire_lead_lock
from ai_sdr.flowengine.audit import record_outbound_audit
from ai_sdr.flowengine.correction import (
    CorrectionEscalation,
    run_guardrails_retry,
    run_transition_retry,
)
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.post_processing import apply_decision
from ai_sdr.flowengine.preprocessing import (
    OptOutDetected,
    resolve_pipeline_context,
)
from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.sender import send_response_text
from ai_sdr.flowengine.system_prompt import (
    CorrectionContext,
    FreshLayer,
    assemble_prompt,
    build_cached_layer,
    build_fresh_layer,
)
from ai_sdr.flowengine.treeflow_loader import TreeflowDef
from ai_sdr.flowengine.usage import accumulate_tokens, extract_usage
from ai_sdr.guardrails.validator import (
    GuardrailConfig,
    validate_response_text,
)
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository

logger = logging.getLogger(__name__)


@dataclass
class RunTurnResult:
    outcome: str  # 'sent' | 'escalated' | 'opt_out' | 'lead_banned' | 'error'
    current_node_after: str | None
    response_text: str | None


@dataclass
class _RoutingStateView:
    """Minimal state view passed to validate_transition.

    Conforms to routing._StateProto. Built per-call so the routing
    layer sees post-decision (collected + extracted_facts) merges.
    """

    collected: dict[str, Any]
    extracted_facts: dict[str, Any]
    objections_handled: list[Any]
    turn_index: int
    active_treatment: Any


async def run_turn(
    session: AsyncSession,
    *,
    tenant: Tenant,
    treeflow: TreeflowDef,
    treeflow_version: TreeflowVersion,
    inbound: InboundMessageRow,
    llm: Runnable,
    adapter: MessagingAdapter,
    opt_out_keywords: list[str],
    guardrail_cfg: GuardrailConfig,
    now: datetime | None = None,
) -> RunTurnResult:
    """Execute one FlowEngine v2 turn. See module docstring."""
    now = now or datetime.now(timezone.utc)

    # [1-3] Preprocessing — resolve Lead, Talk, State; opt-out detection
    try:
        ctx = await resolve_pipeline_context(
            session,
            tenant=tenant,
            inbound=inbound,
            treeflow=treeflow,
            treeflow_version=treeflow_version,
            opt_out_keywords=opt_out_keywords,
        )
    except OptOutDetected:
        logger.info("opt_out_detected_fe01b inbound=%s", inbound.id)
        return RunTurnResult(outcome="opt_out", current_node_after=None, response_text=None)

    # Banned check
    if ctx.lead.risk_level == "banned":
        logger.info("lead_banned_silent_drop lead=%s", ctx.lead.id)
        return RunTurnResult(outcome="lead_banned", current_node_after=None, response_text=None)

    # Per-(tenant, lead) advisory lock for the rest of the turn
    async with session.begin_nested():
        await acquire_lead_lock(session, tenant.id, ctx.lead.id)

        # Load runtime state
        state_repo = TalkFlowStateRepository(session)
        state = await state_repo.load(ctx.talk.id)
        assert state is not None, "TalkFlowState missing after preprocessing"

        # [6] Build layered system prompt
        cached = build_cached_layer(treeflow)
        current_node_def = treeflow.nodes[state.current_node]
        immediate_next = [
            (treeflow.nodes[t.target], t.condition)
            for t in current_node_def.next_nodes
            if t.target in treeflow.nodes
        ]

        inbound_text = (inbound.text or inbound.transcription or "").strip()

        def _fresh(correction: CorrectionContext | None = None) -> FreshLayer:
            return build_fresh_layer(
                current_node=current_node_def,
                immediate_next_nodes=immediate_next,
                collected=state.collected,
                extracted_facts=state.extracted_facts,
                objections_handled=state.objections_handled,
                history=state.messages,
                turn_index=ctx.talk.turn_count + 1,
                now=now,
                active_treatment=state.active_treatment,
                correction=correction,
                current_inbound_text=inbound_text,
            )

        fresh = _fresh(None)
        messages = assemble_prompt(cached, fresh, inbound_text=inbound_text)

        # [7] Main LLM call -> TurnDecision
        decision: TurnDecision = await llm.ainvoke(messages)

        # [8] Validate TurnDecision — guardrails + corrective retry
        validation = validate_response_text(decision.response_text, guardrail_cfg)
        try:
            decision = await run_guardrails_retry(
                initial_decision=decision,
                initial_validation=validation,
                bound_llm=llm,
                cached=cached,
                fresh_builder=lambda c: _fresh(c),
                inbound_text=inbound_text,
                validator_config=guardrail_cfg,
            )
        except CorrectionEscalation as e:
            ctx.talk.status = "requires_review"
            ctx.talk.escalated_at = now
            ctx.talk.escalation_category = "system_exhausted"
            ctx.talk.escalation_reason = str(e)
            logger.warning(
                "turn_escalated_via_guardrails talk=%s reason=%s",
                ctx.talk.id,
                e,
            )
            return RunTurnResult(
                outcome="escalated",
                current_node_after=state.current_node,
                response_text=None,
            )

        # [9] Routing — validate transition
        def _state_view(d: TurnDecision) -> _RoutingStateView:
            return _RoutingStateView(
                collected={**state.collected, **d.collected_fields},
                extracted_facts={**state.extracted_facts, **d.extracted_facts},
                objections_handled=list(state.objections_handled),
                turn_index=ctx.talk.turn_count + 1,
                active_treatment=state.active_treatment,
            )

        resolved_target, failure = validate_transition(
            current_node=state.current_node,
            next_node_suggestion=decision.next_node_suggestion,
            state=_state_view(decision),
            treeflow=treeflow,
        )
        decision, resolved_target = await run_transition_retry(
            initial_decision=decision,
            initial_target=resolved_target,
            initial_failure=failure,
            bound_llm=llm,
            cached=cached,
            fresh_builder=lambda c: _fresh(c),
            inbound_text=inbound_text,
            revalidate=lambda d: validate_transition(
                current_node=state.current_node,
                next_node_suggestion=d.next_node_suggestion,
                state=_state_view(d),
                treeflow=treeflow,
            ),
            current_node=state.current_node,
        )

        # [10] Post-processing — apply decision to state
        await apply_decision(
            session,
            talk=ctx.talk,
            state=state,
            decision=decision,
            resolved_target_node=resolved_target,
            now=now,
        )

        # [11] Token bookkeeping (best-effort)
        tokens = dict(ctx.talk.tokens_consumed or {})
        accumulate_tokens(tokens, extract_usage(getattr(decision, "_raw_message", None)))
        ctx.talk.tokens_consumed = tokens

        # [12] Send to lead via adapter
        send_result = await send_response_text(
            adapter=adapter,
            lead=ctx.lead,
            decision=decision,
        )

        # [13] Audit row
        await record_outbound_audit(
            session,
            talk=ctx.talk,
            inbound=inbound,
            response_text=decision.response_text,
            turn_index=ctx.talk.turn_count,
            send_result=send_result,
            provider=inbound.provider,
            sent_at=now,
        )

    return RunTurnResult(
        outcome="sent",
        current_node_after=resolved_target,
        response_text=decision.response_text,
    )
