"""Cross-objection: new tool objection swaps the current (defers it) (FE-03a Task 21)."""

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


def _tf_with_preco_and_tempo() -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )

    def _obj(oid: str) -> TreeflowObjection:
        return TreeflowObjection(
            id=oid,
            description=f"obj {oid} description text",
            treatment_mode="tool",
            tool_payload=TreeflowToolPayload(
                canonical_arguments_summary="argumentos canonicos longos",
                kb_ref=f"kb_{oid}",
                max_treatment_turns=3,
                resolution_criteria="criterio de resolucao",
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
        global_objections=[_obj("preco"), _obj("tempo")],
    )


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def _active_preco(turn=2):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_new_objection_swaps_current_defers_old():
    state = {"current_node": "a", "active_treatment": _active_preco()}
    decision = _decision(detected_objection="tempo")
    delta = apply(
        state=state,
        decision=decision,
        treeflow=_tf_with_preco_and_tempo(),
    )
    # new active is tempo
    assert delta.new_active_treatment["objection_id"] == "tempo"
    assert delta.new_active_treatment["current_treatment_turn"] == 1
    # preco appended to history as deferred
    assert delta.appended_objection_history == [
        {
            "objection_id": "preco",
            "detected_at_turn": 1,
            "resolved_at_turn": 2,
            "resolution": "deferred",
        }
    ]


def test_swap_emits_cross_swap_event():
    state = {"current_node": "a", "active_treatment": _active_preco()}
    delta = apply(
        state=state,
        decision=_decision(detected_objection="tempo"),
        treeflow=_tf_with_preco_and_tempo(),
    )
    events = dict(delta.events)
    assert "objection.treatment.cross_swap" in events
    assert events["objection.treatment.cross_swap"]["from_id"] == "preco"
    assert events["objection.treatment.cross_swap"]["to_id"] == "tempo"


def test_same_objection_id_is_not_a_swap():
    """detected_objection == active.objection_id should NOT defer."""
    state = {"current_node": "a", "active_treatment": _active_preco()}
    delta = apply(
        state=state,
        decision=_decision(detected_objection="preco"),
        treeflow=_tf_with_preco_and_tempo(),
    )
    assert delta.new_active_treatment["objection_id"] == "preco"
    # Continue, not swap.
    assert delta.appended_objection_history == []
