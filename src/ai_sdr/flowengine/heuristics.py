"""Post-LLM heuristics for the FlowEngine (FE-03a §8 brechas B4 + C4).

These run AFTER the main LLM call and BEFORE objection_runtime.apply().
They detect obvious contradictions between TurnDecision fields and the
response_text, and either correct or just log (no LLM call).
"""

from __future__ import annotations

import re
from typing import Any

from ai_sdr.flowengine.decision import TurnDecision

_DEFERRAL_HINT_RE = re.compile(
    r"\b(pena|pens[ae]r|tanto\s*faz|sei\s*l[áa]|talvez\s*depois|fica\s*pra\s*depois|"
    r"deixa\s*pra\s*l[áa]|t[áa]\s*bom)\b",
    re.IGNORECASE,
)

_COMMITMENT_HINT_RE = re.compile(
    r"\b(vou\s*te\s*enviar|te\s*envio|te\s*conecto|te\s*passo|"
    r"aguarda|pr[óo]ximo\s*passo|agora\s*mesmo)\b",
    re.IGNORECASE,
)


def apply_contradiction_heuristic(
    decision: TurnDecision,
) -> tuple[TurnDecision, list[tuple[str, dict[str, Any]]]]:
    """Brecha B4: degrade resolved_accepted -> resolved_deferred when text contradicts.

    Returns (possibly-modified decision, events list).
    """
    if decision.treatment_status != "resolved_accepted":
        return decision, []

    if not _DEFERRAL_HINT_RE.search(decision.response_text):
        return decision, []

    corrected = decision.model_copy(update={"treatment_status": "resolved_deferred"})
    return corrected, [
        (
            "decision.contradiction_corrected",
            {
                "field": "treatment_status",
                "original": "resolved_accepted",
                "corrected": "resolved_deferred",
                "trigger": "deferral_hint_in_response_text",
            },
        )
    ]


def detect_implicit_transition(
    decision: TurnDecision,
) -> list[tuple[str, dict[str, Any]]]:
    """Brecha C4: log when response_text promises action but next_node is None.

    Pure detection — does NOT modify decision. Returns events list.
    """
    if decision.next_node_suggestion is not None:
        return []
    if not _COMMITMENT_HINT_RE.search(decision.response_text):
        return []
    excerpt = decision.response_text[:120]
    matched = _COMMITMENT_HINT_RE.search(decision.response_text)
    return [
        (
            "decision.implicit_transition_suspected",
            {
                "matched_pattern": matched.group(0) if matched else "",
                "response_excerpt": excerpt,
            },
        )
    ]
