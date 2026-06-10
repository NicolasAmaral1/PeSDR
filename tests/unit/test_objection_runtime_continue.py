"""ACTIVE continues + increments turn when treatment_status=in_progress (FE-03a Task 18)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base: dict = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=1, max_turns=3):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": max_turns,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_in_progress_increments_turn():
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision(treatment_status="in_progress")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.changes_treatment
    assert delta.new_active_treatment["current_treatment_turn"] == 2


def test_in_progress_emits_continued_event():
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision(treatment_status="in_progress")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    event_names = [name for name, _ in delta.events]
    assert "objection.treatment.continued" in event_names


def test_missing_treatment_status_assumes_in_progress():
    """Defensive: if LLM forgets to emit, runtime assumes in_progress (conservative)."""
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision()  # treatment_status=None (default)
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.changes_treatment
    assert delta.new_active_treatment["current_treatment_turn"] == 2
