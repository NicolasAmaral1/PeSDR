"""ACTIVE -> IDLE on resolved_accepted/resolved_deferred (FE-03a Task 19)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=2):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_resolved_accepted_goes_idle_with_history_accepted():
    state = {"current_node": "a", "active_treatment": _active()}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history == [
        {
            "objection_id": "preco",
            "detected_at_turn": 1,
            "resolved_at_turn": 2,
            "resolution": "accepted",
        }
    ]


def test_resolved_deferred_goes_idle_with_history_deferred():
    state = {"current_node": "a", "active_treatment": _active()}
    decision = _decision(treatment_status="resolved_deferred")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history[0]["resolution"] == "deferred"


def test_resolved_emits_event_with_status_and_turn_count():
    state = {"current_node": "a", "active_treatment": _active(turn=2)}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    events = dict(delta.events)
    assert "objection.treatment.resolved" in events
    payload = events["objection.treatment.resolved"]
    assert payload["status"] == "accepted"
    assert payload["total_turns"] == 2
