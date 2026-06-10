"""Resolve TurnDecision.request_human_escalation -> requires_review_reason (FE-03a Task 26).

The LLM emits `request_human_escalation` as a structured HumanEscalation
object whenever it (or the lead) wants a human teammate. All categories
(lead_requested, complex_objection, etc.) collapse to one DB reason:
'escalation_requested'. Category + urgency stay on talk.escalation_category
for HITL prioritization (existing FE-01b columns).
"""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision


def resolve_escalation_reason(decision: TurnDecision) -> str | None:
    if decision.request_human_escalation is None:
        return None
    return "escalation_requested"
