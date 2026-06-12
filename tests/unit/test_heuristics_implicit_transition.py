"""Heurística C4: detecta committal text sem next_node (FE-03a Task 24)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.heuristics import detect_implicit_transition


def _decision(**kw):
    base = {"response_text": "x", "collected_fields": {}, "reasoning": "r"}
    base.update(kw)
    return TurnDecision(**base)


def test_committal_text_without_transition_emits_event():
    d = _decision(
        response_text="Beleza, vou te enviar o link agora mesmo",
        next_node_suggestion=None,
    )
    events = detect_implicit_transition(d)
    assert events
    name, payload = events[0]
    assert name == "decision.implicit_transition_suspected"
    assert "vou te enviar" in payload["matched_pattern"].lower()


def test_committal_text_WITH_transition_emits_nothing():
    d = _decision(
        response_text="Beleza, vou te enviar o link agora mesmo",
        next_node_suggestion="envio_checkout",
    )
    events = detect_implicit_transition(d)
    assert events == []


def test_non_committal_text_emits_nothing():
    d = _decision(
        response_text="Entendi. Pode me contar mais sobre o seu negócio?",
        next_node_suggestion=None,
    )
    events = detect_implicit_transition(d)
    assert events == []
