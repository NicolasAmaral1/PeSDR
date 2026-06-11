"""apply_decision mutates state + Talk consistently."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
    TreeflowTransition,
)
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


def _tf() -> TreeflowDef:
    node = TreeflowNode(
        id="saudacao",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="saudacao")],
    )
    qual = TreeflowNode(
        id="qualificacao",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="qualificacao")],
    )
    preco_inline = TreeflowObjection(
        id="preco",
        description="lead reclama de preco",
        treatment_mode="inline",
        tool_payload=None,
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="saudacao",
        nodes={"saudacao": node, "qualificacao": qual},
        global_objections=[preco_inline],
    )


async def _seed(db_session: AsyncSession) -> tuple[Talk, TalkFlowState]:
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
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()
    state = TalkFlowState(
        talk_id=talk.id,
        tenant_id=tenant.id,
        current_node="saudacao",
        collected={"segmento": "saas"},
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
        talkflow_stack=[],
    )
    db_session.add(state)
    await db_session.flush()
    return talk, state


@pytest.mark.asyncio
async def test_merges_collected_fields_and_facts(db_session: AsyncSession) -> None:
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="oi! qual seu volume?",
        collected_fields={"canal": "google_ads"},
        extracted_facts={"tem_filha_8_anos": True},
        reasoning="r",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="qualificacao",
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
        treeflow=_tf(),
    )
    await db_session.flush()
    assert state.collected == {"segmento": "saas", "canal": "google_ads"}
    assert state.extracted_facts == {"tem_filha_8_anos": True}
    assert state.current_node == "qualificacao"


@pytest.mark.asyncio
async def test_appends_assistant_message_and_bumps_turn(
    db_session: AsyncSession,
) -> None:
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="oi! qual seu volume?",
        collected_fields={},
        reasoning="r",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="saudacao",
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
        treeflow=_tf(),
    )
    await db_session.flush()
    assert len(state.messages) == 2
    assert state.messages[-1]["role"] == "assistant"
    assert state.messages[-1]["content"] == "oi! qual seu volume?"
    assert state.messages[-1]["source"] == "agent"
    assert talk.turn_count == 1
    assert talk.last_message_at == datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_inline_objection_does_not_enter_state_or_history(
    db_session: AsyncSession,
) -> None:
    """FE-03a §4: inline mode is LLM-only — never sets active_treatment, never
    appends to objections_handled. Only `tool` mode crosses the state machine.
    """
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="entendo a preocupacao com preco...",
        collected_fields={},
        reasoning="r",
        detected_objection="preco",
        treatment_strategy="inline",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="saudacao",
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
        treeflow=_tf(),
    )
    await db_session.flush()
    assert state.objections_handled == []
    assert state.active_treatment is None


@pytest.mark.asyncio
async def test_close_talk_signal_is_logged_only(db_session: AsyncSession) -> None:
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="combinado! ate breve.",
        collected_fields={},
        reasoning="r",
        suggest_close_talk="completed_success",
    )
    await apply_decision(
        db_session,
        talk=talk,
        state=state,
        decision=decision,
        resolved_target_node="saudacao",
        now=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
        treeflow=_tf(),
    )
    await db_session.flush()
    # FE-01b is a no-op on closure; FE-03 wires Talk.status transitions.
    assert talk.status == "active"
    assert talk.closed_at is None
