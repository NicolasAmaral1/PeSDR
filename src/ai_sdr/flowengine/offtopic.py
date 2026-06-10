"""Off-topic counter + escalation (FE-03a §8 A1).

The LLM is told (via system prompt) to flag inbounds that fall outside
the funnel's scope. This module increments TalkFlowState.off_topic_count
and decides when to escalate.
"""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision

OFFTOPIC_THRESHOLD = 3


def handle_offtopic(
    *,
    current_count: int,
    decision: TurnDecision,
) -> tuple[int, str | None]:
    """Return (new_count, requires_review_reason_or_None)."""
    if not decision.off_topic_detected:
        return current_count, None
    new_count = current_count + 1
    if new_count >= OFFTOPIC_THRESHOLD:
        return new_count, "off_topic_exhausted"
    return new_count, None
