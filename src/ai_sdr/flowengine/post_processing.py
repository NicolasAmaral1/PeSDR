"""Apply TurnDecision to persistent state (FE-03a Task 27 — extended).

Pipeline:
  1. Run contradiction heuristic on decision
  2. Run implicit-transition heuristic (events only)
  3. Compute objection_runtime.StateDelta
  4. Apply collected_fields + extracted_facts merge
  5. Apply state delta (active_treatment, objection history)
  6. Set current_node to resolved_target
  7. Append assistant message to history window
  8. Set turn_count + last_message_at on talk
  9. Set requires_review_reason if any heuristic raised it
  10. Emit all collected events via structlog
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import ai_sdr.flowengine.actions  # noqa: F401 — side-effect: register adapters
from ai_sdr.flowengine.actions.dispatcher import dispatch_actions
from ai_sdr.flowengine.close_lifecycle import evaluate_completion_rule
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.escalation import resolve_escalation_reason
from ai_sdr.flowengine.heuristics import (
    apply_contradiction_heuristic,
    detect_implicit_transition,
)
from ai_sdr.flowengine.objection_runtime import apply as apply_objection_state
from ai_sdr.flowengine.offtopic import handle_offtopic
from ai_sdr.flowengine.state import Message
from ai_sdr.flowengine.treeflow_loader import TreeflowDef
from ai_sdr.models.review_reason import RequiresReviewReason
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository

logger = logging.getLogger(__name__)


def _emit_events(
    events: list[tuple[str, dict[str, Any]]],
    talk_id: Any,
    lead_id: Any,
) -> None:
    for name, payload in events:
        logger.info(
            "fe03a_event %s talk=%s lead=%s payload=%s",
            name,
            talk_id,
            lead_id,
            payload,
        )


async def _load_lead_for_actions(session: AsyncSession, lead_id: Any) -> Any:
    """Lazy lead lookup used only when a node has on_collected actions to fire."""
    from ai_sdr.models.lead import Lead

    return await session.get(Lead, lead_id)


async def apply_decision(
    session: AsyncSession,
    *,
    talk: Talk,
    state: TalkFlowState,
    decision: TurnDecision,
    resolved_target_node: str,
    now: datetime,
    treeflow: TreeflowDef,
) -> None:
    """Mutate state + talk to reflect the LLM's decision."""
    events: list[tuple[str, dict[str, Any]]] = []

    # 1. Contradiction heuristic (B4)
    decision, ev = apply_contradiction_heuristic(decision)
    events.extend(ev)

    # 2. Implicit-transition heuristic (C4, audit only)
    events.extend(detect_implicit_transition(decision))

    # 3. Compute objection state delta
    state_view = {
        "current_node": state.current_node,
        "active_treatment": state.active_treatment,
        "objections_handled": list(state.objections_handled),
    }
    delta = apply_objection_state(
        state=state_view,
        decision=decision,
        treeflow=treeflow,
    )
    events.extend(delta.events)

    # 4. Merge collected + extracted_facts
    if decision.collected_fields:
        merged = dict(state.collected)
        merged.update(decision.collected_fields)
        state.collected = merged
        flag_modified(state, "collected")
    if decision.extracted_facts:
        merged_facts = dict(state.extracted_facts)
        merged_facts.update(decision.extracted_facts)
        state.extracted_facts = merged_facts
        flag_modified(state, "extracted_facts")

    # 4b. FE-03c: dispatch on_collected actions for the (pre-transition) node.
    # state.current_node still points at the node where the LLM emitted the
    # collected_fields — that's the node whose on_collected we want to fire.
    node_spec_for_actions = treeflow.nodes.get(state.current_node)
    if node_spec_for_actions is not None and getattr(node_spec_for_actions, "on_collected", []):
        lead_for_actions = await _load_lead_for_actions(session, talk.lead_id)
        if lead_for_actions is not None:
            from ai_sdr.worker.queue import enqueue_execute_action

            await dispatch_actions(
                session=session,
                repo=ActionExecutionRepository(session),
                enqueue=enqueue_execute_action,
                state=state,
                decision=decision,
                node_spec=node_spec_for_actions,
                talk=talk,
                lead=lead_for_actions,
            )

    # 5. Apply state delta
    if delta.changes_treatment:
        # changes_treatment guards against the _UNSET sentinel.
        state.active_treatment = cast("dict[str, Any] | None", delta.new_active_treatment)
        flag_modified(state, "active_treatment")
    if delta.appended_objection_history:
        history = list(state.objections_handled)
        history.extend(delta.appended_objection_history)
        state.objections_handled = history
        flag_modified(state, "objections_handled")

    # 6. current_node
    state.current_node = resolved_target_node

    # 7. History append
    repo = TalkFlowStateRepository(session)
    next_turn = talk.turn_count + 1
    await repo.append_message(
        state,
        Message(
            role="assistant",
            content=decision.response_text,
            source="agent",
            turn_index=next_turn,
            timestamp=now,
        ),
        max_window=15,
    )

    # 8. Talk metadata
    talk.turn_count = next_turn
    talk.last_message_at = now

    # NEW (FE-03b §5.4): completion rule check.
    # Mutually exclusive with requires_review_reason — when a rule fires,
    # the Talk closes and the review chain is SKIPPED.
    close_outcome = evaluate_completion_rule(
        state=state,
        decision=decision,
        treeflow=treeflow,
    )
    if close_outcome is not None:
        talk.status = cast(Any, close_outcome.status)
        talk.closed_at = now
        talk.closed_reason = close_outcome.reason
        talk.closed_by = close_outcome.closed_by
        logger.info(
            "talk.closed.completion talk=%s outcome=%s rule=%s",
            talk.id,
            close_outcome.status,
            close_outcome.reason,
        )
        # Skip the requires_review_reason chain — completion close wins.
        _emit_events(events, talk.id, getattr(talk, "lead_id", None))
        return

    # 9. requires_review_reason — first non-None wins.
    # Objection-treatment exhaustion has highest priority (set by runtime),
    # then LLM-requested escalation, then off-topic threshold.
    review_reason = delta.requires_review_reason or resolve_escalation_reason(decision)

    # Off-topic counter lives in state.collected['__off_topic_count__'] (T2 shadow key).
    current_offtopic_raw = (
        state.collected.get("__off_topic_count__") if isinstance(state.collected, dict) else 0
    )
    current_offtopic = current_offtopic_raw or 0
    new_offtopic, offtopic_reason = handle_offtopic(
        current_count=current_offtopic,
        decision=decision,
    )
    if new_offtopic != current_offtopic:
        merged = dict(state.collected)
        merged["__off_topic_count__"] = new_offtopic
        state.collected = merged
        flag_modified(state, "collected")
    review_reason = review_reason or offtopic_reason

    if review_reason and talk.status != "requires_review":
        talk.status = "requires_review"
        talk.requires_review_reason = cast(RequiresReviewReason, review_reason)
        talk.escalated_at = now

    # 10. Emit events
    _emit_events(events, talk.id, getattr(talk, "lead_id", None))

    if decision.suggest_close_talk != "no":
        logger.info(
            "talk_close_signal_ignored_in_fe03a talk_id=%s signal=%s",
            talk.id,
            decision.suggest_close_talk,
        )
