"""Off-topic counter + escalate on 3rd strike (FE-03a Task 25)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.offtopic import (
    OFFTOPIC_THRESHOLD,
    handle_offtopic,
)


def _decision(off_topic: bool):
    return TurnDecision(
        response_text="redirecionando",
        collected_fields={},
        reasoning="r",
        off_topic_detected=off_topic,
    )


def test_offtopic_increments_counter():
    new_count, reason = handle_offtopic(current_count=0, decision=_decision(True))
    assert new_count == 1
    assert reason is None


def test_offtopic_below_threshold_does_not_escalate():
    new_count, reason = handle_offtopic(
        current_count=OFFTOPIC_THRESHOLD - 2, decision=_decision(True),
    )
    assert new_count == OFFTOPIC_THRESHOLD - 1
    assert reason is None


def test_offtopic_at_threshold_escalates():
    new_count, reason = handle_offtopic(
        current_count=OFFTOPIC_THRESHOLD - 1, decision=_decision(True),
    )
    assert new_count == OFFTOPIC_THRESHOLD
    assert reason == "off_topic_exhausted"


def test_not_offtopic_does_not_increment():
    new_count, reason = handle_offtopic(
        current_count=2, decision=_decision(False),
    )
    assert new_count == 2
    assert reason is None
