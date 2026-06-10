"""Escalation handler — lead_requested vs LLM-decided (FE-03a Task 26)."""

from __future__ import annotations

from ai_sdr.flowengine.decision import HumanEscalation, TurnDecision
from ai_sdr.flowengine.escalation import resolve_escalation_reason


def _decision(escalation: HumanEscalation | None = None) -> TurnDecision:
    return TurnDecision(
        response_text="x",
        collected_fields={},
        reasoning="r",
        request_human_escalation=escalation,
    )


def test_lead_requested_resolves_to_escalation_requested():
    esc = HumanEscalation(
        reason="lead asked to talk to a human directly",
        category="lead_requested",
        urgency="medium",
    )
    reason = resolve_escalation_reason(_decision(esc))
    assert reason == "escalation_requested"


def test_llm_decided_escalation_resolves_to_escalation_requested():
    esc = HumanEscalation(
        reason="objection treatment not making progress",
        category="complex_objection",
        urgency="medium",
    )
    reason = resolve_escalation_reason(_decision(esc))
    assert reason == "escalation_requested"


def test_no_escalation_resolves_to_none():
    assert resolve_escalation_reason(_decision(None)) is None
