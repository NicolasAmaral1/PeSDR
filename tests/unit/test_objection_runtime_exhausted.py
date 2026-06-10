"""Max turns exhausted: gracefully_continue OR escalate_to_human (FE-03a Task 20)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)


def _tf_with_action(action: str) -> TreeflowDef:
    n = TreeflowNode(
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
            canonical_arguments_summary="ROI cabe em 1 mês",
            kb_ref="kb_preco",
            max_treatment_turns=3,
            resolution_criteria="lead aceitou parcelamento",
            on_max_turns_no_resolution=TreeflowOnMaxTurns(action=action),
        ),
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n},
        global_objections=[preco],
    )


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=3, max_turns=3):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": max_turns,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_exhausted_with_gracefully_continue_goes_idle_no_review():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state,
        decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("gracefully_continue"),
    )
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history[0]["resolution"] == "exhausted"
    assert delta.requires_review_reason is None


def test_exhausted_with_escalate_sets_review_reason():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state,
        decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("escalate_to_human"),
    )
    assert delta.new_active_treatment is None
    assert delta.requires_review_reason == "objection_treatment_exhausted"


def test_exhausted_emits_event_with_action_taken():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state,
        decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("escalate_to_human"),
    )
    events = dict(delta.events)
    assert "objection.treatment.exhausted" in events
    assert events["objection.treatment.exhausted"]["action_taken"] == "escalate_to_human"
