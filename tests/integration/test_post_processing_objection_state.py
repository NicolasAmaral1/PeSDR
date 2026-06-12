"""apply_decision integrates objection_runtime + heuristics (FE-03a Task 27).

Wires the post-LLM pipeline: contradiction heuristic -> implicit-transition
heuristic -> objection_runtime.apply -> state delta -> requires_review_reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.post_processing import apply_decision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


def _treeflow_with_preco_tool() -> TreeflowDef:
    node = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    preco = TreeflowObjection(
        id="preco",
        description="lead diz preço caro",
        treatment_mode="tool",
        tool_payload=TreeflowToolPayload(
            canonical_arguments_summary="ROI cabe em 1 mes",
            kb_ref="kb_preco",
            max_treatment_turns=3,
            resolution_criteria="lead aceitou parcelamento",
            on_max_turns_no_resolution=TreeflowOnMaxTurns(action="gracefully_continue"),
        ),
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": node},
        global_objections=[preco],
    )


async def _seed(
    db_session: AsyncSession,
    *,
    current_node: str = "a",
    active_treatment: dict | None = None,
) -> tuple[Talk, TalkFlowState]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="tf",
        version="1",
        content_hash="x",
        content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(talk)
    await db_session.flush()
    state = TalkFlowState(
        talk_id=talk.id,
        tenant_id=tenant.id,
        current_node=current_node,
        collected={},
        extracted_facts={},
        messages=[
            {
                "role": "user",
                "content": "oi",
                "source": "lead",
                "turn_index": 1,
                "timestamp": "2026-06-02T10:00:00+00:00",
                "media_type": "text",
            }
        ],
        objections_handled=[],
        active_treatment=active_treatment,
        talkflow_stack=[],
    )
    db_session.add(state)
    await db_session.flush()
    return talk, state


@pytest.mark.asyncio
async def test_objection_detected_enters_active_treatment(
    db_session: AsyncSession,
) -> None:
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="argumento sobre o preco",
        collected_fields={},
        reasoning="r",
        detected_objection="preco",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="a",
        now=datetime.now(UTC),
        treeflow=_treeflow_with_preco_tool(),
    )
    await db_session.flush()
    assert state.active_treatment is not None
    assert state.active_treatment["objection_id"] == "preco"
    assert state.active_treatment["current_treatment_turn"] == 1
    assert state.active_treatment["max_treatment_turns"] == 3


@pytest.mark.asyncio
async def test_contradiction_heuristic_applied_before_state_update(
    db_session: AsyncSession,
) -> None:
    active = {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": 2,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }
    talk, state = await _seed(db_session, active_treatment=active)
    decision = TurnDecision(
        response_text="Ah que pena, deixa eu te deixar pensar entao",
        collected_fields={},
        reasoning="r",
        treatment_status="resolved_accepted",  # contradicts text
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="a",
        now=datetime.now(UTC),
        treeflow=_treeflow_with_preco_tool(),
    )
    await db_session.flush()
    # Contradiction corrected: accepted -> deferred -> active cleared, history deferred
    assert state.active_treatment is None
    history = state.objections_handled
    assert history[-1]["resolution"] == "deferred"


@pytest.mark.asyncio
async def test_objection_treatment_exhausted_sets_review_reason(
    db_session: AsyncSession,
) -> None:
    """Exhausted treatment with escalate_to_human action flips talk.status."""
    tf = _treeflow_with_preco_tool()
    # Reconfigure to escalate_to_human on max turns
    tf.global_objections[0].tool_payload.on_max_turns_no_resolution = TreeflowOnMaxTurns(
        action="escalate_to_human"
    )
    active = {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": 3,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }
    talk, state = await _seed(db_session, active_treatment=active)
    decision = TurnDecision(
        response_text="continuamos conversando",
        collected_fields={},
        reasoning="r",
        treatment_status="in_progress",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="a",
        now=datetime.now(UTC),
        treeflow=tf,
    )
    await db_session.flush()
    assert state.active_treatment is None
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "objection_treatment_exhausted"


@pytest.mark.asyncio
async def test_offtopic_increments_and_escalates_at_threshold(
    db_session: AsyncSession,
) -> None:
    talk, state = await _seed(db_session)
    # Pre-seed counter at 2 (threshold = 3)
    state.collected = {"__off_topic_count__": 2}
    await db_session.flush()
    decision = TurnDecision(
        response_text="vamos voltar ao foco",
        collected_fields={},
        reasoning="r",
        off_topic_detected=True,
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="a",
        now=datetime.now(UTC),
        treeflow=_treeflow_with_preco_tool(),
    )
    await db_session.flush()
    assert state.collected["__off_topic_count__"] == 3
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "off_topic_exhausted"
