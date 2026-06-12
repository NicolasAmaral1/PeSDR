"""TurnDecision is the single structured output of the main LLM call."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.decision import (
    HumanEscalation,
    JudgeVerdict,
    TurnDecision,
)


def test_minimal_valid_turn_decision() -> None:
    d = TurnDecision(
        response_text="oi! qual seu segmento?",
        collected_fields={"segmento": "saas"},
        reasoning="greeted lead and extracted segment",
    )
    assert d.response_text == "oi! qual seu segmento?"
    assert d.collected_fields == {"segmento": "saas"}
    assert d.intends_to_advance is False
    assert d.suggest_close_talk == "no"
    assert d.suspect_injection_attempt is False
    assert d.request_human_escalation is None


def test_response_text_min_length() -> None:
    with pytest.raises(ValidationError):
        TurnDecision(
            response_text="",
            collected_fields={},
            reasoning="x",
        )


def test_reasoning_overflow_truncates_instead_of_failing() -> None:
    """LLMs routinely overshoot the cap; reasoning is internal telemetry,
    so the validator truncates to 400 instead of failing the whole turn."""
    d = TurnDecision(
        response_text="oi",
        collected_fields={},
        reasoning="x" * 500,  # > 400 char soft cap
    )
    assert len(d.reasoning) == 400
    assert d.reasoning.endswith("...")


def test_human_escalation_categories_validated() -> None:
    e = HumanEscalation(
        reason="lead asked complex question I can't answer",
        category="unknown_info",
        urgency="medium",
    )
    assert e.category == "unknown_info"
    with pytest.raises(ValidationError):
        HumanEscalation(
            reason="reason ok",
            category="not_a_category",  # type: ignore[arg-type]
            urgency="medium",
        )


def test_turn_decision_with_escalation() -> None:
    d = TurnDecision(
        response_text="Vou conferir com a equipe e te volto",
        collected_fields={},
        reasoning="lead asked about regulatory edge case beyond scope",
        request_human_escalation=HumanEscalation(
            reason="regulatory question outside training data",
            category="out_of_scope",
            urgency="medium",
            waiting_message="vou conferir e volto",
        ),
    )
    assert d.request_human_escalation is not None
    assert d.request_human_escalation.urgency == "medium"


def test_judge_verdict_round_trip() -> None:
    v = JudgeVerdict(should_exit=True, reasoning="all qualifying fields collected")
    reloaded = JudgeVerdict.model_validate(v.model_dump(mode="json"))
    assert reloaded.should_exit is True


def test_suggest_close_talk_literals() -> None:
    """Closure signal only accepts known closure types."""
    d = TurnDecision(
        response_text="combinado, te aviso!",
        collected_fields={},
        reasoning="lead confirmed demo",
        suggest_close_talk="completed_success",
    )
    assert d.suggest_close_talk == "completed_success"
    with pytest.raises(ValidationError):
        TurnDecision(
            response_text="x",
            collected_fields={},
            reasoning="y",
            suggest_close_talk="random",  # type: ignore[arg-type]
        )
