"""Pre-fabricated TurnDecisions for pipeline tests."""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision


def greeting_decision() -> TurnDecision:
    return TurnDecision(
        response_text="oi! qual seu segmento de negocio?",
        collected_fields={},
        reasoning="greeted lead, asked segment",
        intends_to_advance=False,
    )


def collect_segment_decision() -> TurnDecision:
    return TurnDecision(
        response_text="legal! qual seu ticket medio?",
        collected_fields={"segmento": "saas"},
        reasoning="captured segmento; asking ticket",
        next_node_suggestion="qualificacao",
        intends_to_advance=True,
    )
