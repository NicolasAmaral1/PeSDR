"""post_processing applies completion close (FE-03b Task 12).

Marked integration: requires Postgres (db_session fixture). Skipped locally
without docker; runs on VPS via `make test-integration` or unfiltered.

The spec plan uses hypothetical `talk_factory` / `talkflow_state_factory`
fixtures that don't yet exist in the repo — adapted here to the existing
inline-seed pattern used by FE-03a T27's test_post_processing_objection_state.
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
    TreeflowCompletionRule,
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTalkLifecycle,
    TreeflowTransition,
)
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


def _treeflow(rules: list[TreeflowCompletionRule]) -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n},
        talk_lifecycle=TreeflowTalkLifecycle(close_when_completed=rules),
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
        last_message_at=datetime.now(UTC),
    )
    db_session.add(talk)
    await db_session.flush()
    state = TalkFlowState(
        talk_id=talk.id,
        tenant_id=tenant.id,
        current_node="a",
        collected={},
        extracted_facts={},
        messages=[],
        objections_handled=[],
        active_treatment=None,
        talkflow_stack=[],
    )
    db_session.add(state)
    await db_session.flush()
    return talk, state


@pytest.mark.asyncio
async def test_completion_rule_sets_talk_status_and_skips_review_chain(
    db_session: AsyncSession,
) -> None:
    rules = [
        TreeflowCompletionRule(
            expression="collected.demo_agendada == True",
            outcome="success",
        )
    ]
    tf = _treeflow(rules)
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="Maravilha! Vou agendar.",
        collected_fields={"demo_agendada": True},
        reasoning="r",
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
    assert talk.status == "closed_completed_success"
    assert talk.closed_reason is not None
    assert talk.closed_by == "pipeline_hook"
    assert talk.requires_review_reason is None


@pytest.mark.asyncio
async def test_no_completion_rule_does_not_close_talk(
    db_session: AsyncSession,
) -> None:
    tf = _treeflow([])
    talk, state = await _seed(db_session)
    decision = TurnDecision(
        response_text="x",
        collected_fields={},
        reasoning="r",
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
    assert talk.status == "active"
