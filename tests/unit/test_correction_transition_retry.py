"""run_transition_retry — corrective retry on invalid transition."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_sdr.flowengine.correction import run_transition_retry
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.system_prompt import CachedLayer, FreshLayer


def _td(text: str, next_node: str | None = None) -> TurnDecision:
    return TurnDecision(
        response_text=text,
        collected_fields={},
        reasoning="r",
        next_node_suggestion=next_node,
        intends_to_advance=next_node is not None,
    )


@pytest.mark.asyncio
async def test_no_failure_returns_decision_and_target() -> None:
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(side_effect=AssertionError("should not retry"))
    decision, target = await run_transition_retry(
        initial_decision=_td("oi", next_node="b"),
        initial_target="b",
        initial_failure=None,
        bound_llm=bound_llm,
        cached=CachedLayer(text="C"),
        fresh_builder=lambda _c: FreshLayer(text="F"),
        inbound_text="oi",
        revalidate=lambda d: ("b", None),
        current_node="a",
    )
    assert decision.response_text == "oi"
    assert target == "b"


@pytest.mark.asyncio
async def test_invalid_transition_triggers_retry_succeeds() -> None:
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(return_value=_td("ok", next_node="b"))
    decision, target = await run_transition_retry(
        initial_decision=_td("oi", next_node="ghost"),
        initial_target="a",
        initial_failure="invalid_target",
        bound_llm=bound_llm,
        cached=CachedLayer(text="C"),
        fresh_builder=lambda _c: FreshLayer(text="F"),
        inbound_text="oi",
        revalidate=lambda d: ("b", None),
        current_node="a",
    )
    assert target == "b"
    assert decision.response_text == "ok"
    bound_llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_failure_falls_back_to_stay() -> None:
    """If retry STILL fails, stay in current_node + send original response_text."""
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(return_value=_td("retry response", next_node="ghost2"))
    decision, target = await run_transition_retry(
        initial_decision=_td("oi original", next_node="ghost1"),
        initial_target="a",
        initial_failure="invalid_target",
        bound_llm=bound_llm,
        cached=CachedLayer(text="C"),
        fresh_builder=lambda _c: FreshLayer(text="F"),
        inbound_text="oi",
        revalidate=lambda d: ("a", "invalid_target"),
        current_node="a",
    )
    assert target == "a"
    # We send the ORIGINAL response_text — not the retry's, which was also bad.
    assert decision.response_text == "oi original"
    bound_llm.ainvoke.assert_awaited_once()
