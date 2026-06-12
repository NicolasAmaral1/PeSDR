"""Heurística B4: corrige accepted -> deferred quando texto contradiz (FE-03a Task 23)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.heuristics import apply_contradiction_heuristic


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def test_corrects_accepted_when_text_says_pena_pensar():
    d = _decision(
        response_text="Ah que pena, deixa eu te deixar pensar então",
        treatment_status="resolved_accepted",
    )
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_deferred"
    assert any(name == "decision.contradiction_corrected" for name, _ in events)


def test_corrects_accepted_when_text_says_tanto_faz():
    d = _decision(
        response_text="Tanto faz, vai. Te mando o material depois",
        treatment_status="resolved_accepted",
    )
    corrected, _ = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_deferred"


def test_does_not_correct_clearly_positive_acceptance():
    d = _decision(
        response_text="Fechou! Vou agendar agora",
        treatment_status="resolved_accepted",
    )
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_accepted"
    assert not events


def test_no_op_when_treatment_status_is_none():
    d = _decision(treatment_status=None)
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status is None
    assert not events
