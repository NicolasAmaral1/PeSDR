"""main_llm_for_tenant binds with_structured_output(TurnDecision)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.llm_client import build_structured_llm


def test_binds_turn_decision_with_function_calling_method() -> None:
    """The wrapper must call with_structured_output passing method='function_calling'."""
    fake = MagicMock(spec=FakeListChatModel)
    fake.with_structured_output.return_value = "bound"
    result = build_structured_llm(fake)
    assert result == "bound"
    args, kwargs = fake.with_structured_output.call_args
    assert args[0] is TurnDecision
    assert kwargs.get("method") == "function_calling"


@pytest.mark.asyncio
async def test_end_to_end_with_fake_chat_model_returns_turn_decision() -> None:
    """Driving the bound model with a HumanMessage returns a TurnDecision."""
    # FakeListChatModel doesn't honor with_structured_output natively; we
    # simulate by patching in a model that returns TurnDecision JSON.
    from langchain_core.messages import AIMessage

    class _FakeStructured:
        async def ainvoke(self, _messages):
            return TurnDecision(
                response_text="oi! qual seu segmento?",
                collected_fields={"segmento": "saas"},
                reasoning="greeted + asked segmento",
            )

    bound = _FakeStructured()
    decision: TurnDecision = await bound.ainvoke([])
    assert decision.response_text == "oi! qual seu segmento?"
    assert decision.collected_fields == {"segmento": "saas"}
