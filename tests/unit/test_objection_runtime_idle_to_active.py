"""IDLE -> ACTIVE: LLM detected an objection with treatment_mode=tool (FE-03a Task 17)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import StateDelta, apply
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)


def _tf_with_preco_tool() -> TreeflowDef:
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
            on_max_turns_no_resolution=TreeflowOnMaxTurns(action="gracefully_continue"),
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


def _decision(**kwargs) -> TurnDecision:
    base: dict = {
        "response_text": "argumento",
        "collected_fields": {},
        "reasoning": "r",
    }
    base.update(kwargs)
    return TurnDecision(**base)


def test_idle_enters_active_on_detected_tool_objection():
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection="preco")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert isinstance(delta, StateDelta)
    assert delta.new_active_treatment is not None
    assert delta.new_active_treatment["objection_id"] == "preco"
    assert delta.new_active_treatment["current_treatment_turn"] == 1
    assert delta.new_active_treatment["max_treatment_turns"] == 3


def test_idle_stays_idle_when_no_objection_detected():
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection=None)
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None


def test_idle_does_not_enter_for_inline_objection():
    tf = _tf_with_preco_tool()
    # Add a second objection in inline mode and detect it.
    inline_obj = TreeflowObjection(
        id="downsell",
        description="lead pede algo mais barato",
        treatment_mode="inline",
        tool_payload=None,
    )
    tf.global_objections.append(inline_obj)
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection="downsell")
    delta = apply(state=state, decision=decision, treeflow=tf)
    assert delta.new_active_treatment is None
