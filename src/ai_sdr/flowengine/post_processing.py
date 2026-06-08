"""Apply TurnDecision to persistent state after validation.

Pure mutations + flush. The orchestrator (Task 17) calls this AFTER:
  - validate_transition picked the resolved_target_node
  - guardrails passed (possibly after retry)
  - token usage was tallied into Talk.tokens_consumed
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.state import Message
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository

logger = logging.getLogger(__name__)


async def apply_decision(
    session: AsyncSession,
    *,
    talk: Talk,
    state: TalkFlowState,
    decision: TurnDecision,
    resolved_target_node: str,
    now: datetime,
) -> None:
    """Mutate state + talk to reflect the LLM's decision."""
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

    if decision.detected_objection:
        history = list(state.objections_handled)
        history.append({
            "objection_id": decision.detected_objection,
            "detected_at_turn": talk.turn_count + 1,
            "resolved_at_turn": None,
            "resolution": None,
        })
        state.objections_handled = history
        flag_modified(state, "objections_handled")

    state.current_node = resolved_target_node

    repo = TalkFlowStateRepository(session)
    next_turn = talk.turn_count + 1
    assistant_msg = Message(
        role="assistant",
        content=decision.response_text,
        source="agent",
        turn_index=next_turn,
        timestamp=now,
    )
    await repo.append_message(state, assistant_msg, max_window=15)

    talk.turn_count = next_turn
    talk.last_message_at = now

    if decision.suggest_close_talk != "no":
        # FE-01b: log only. FE-03 implements lifecycle close transitions.
        logger.info(
            "talk_close_signal_ignored_in_fe01b talk_id=%s signal=%s",
            talk.id,
            decision.suggest_close_talk,
        )
