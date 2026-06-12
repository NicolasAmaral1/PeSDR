"""Defensive cases: hallucinated id, treatment_status when IDLE (FE-03a Task 22)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def test_hallucinated_objection_id_is_ignored_emits_event():
    """LLM emits objection id that doesn't exist in YAML — ignore + log."""
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(detected_objection="xpto_nao_existe")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert not delta.changes_treatment or delta.new_active_treatment is None
    events = dict(delta.events)
    assert "objection.hallucinated_id" in events
    assert events["objection.hallucinated_id"]["id_received"] == "xpto_nao_existe"


def test_treatment_status_when_idle_is_ignored():
    """treatment_status only valid during ACTIVE — must be ignored when IDLE."""
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None or delta.changes_treatment is False


def test_inline_objection_detected_does_not_change_state_but_logs_nothing_special():
    """Inline mode just emits no treatment event; not hallucination."""
    from ai_sdr.flowengine.treeflow_loader import TreeflowObjection

    tf = _tf_with_preco_tool()
    tf.global_objections.append(
        TreeflowObjection(
            id="curiosidade",
            description="lead pergunta algo lateral sobre vc",
            treatment_mode="inline",
            tool_payload=None,
        )
    )
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(detected_objection="curiosidade")
    delta = apply(state=state, decision=decision, treeflow=tf)
    event_names = [n for n, _ in delta.events]
    assert "objection.hallucinated_id" not in event_names
    assert "objection.treatment.entered" not in event_names
