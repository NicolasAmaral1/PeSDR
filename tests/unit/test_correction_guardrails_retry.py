"""run_guardrails_retry orchestrates one corrective retry max."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from ai_sdr.flowengine.correction import (
    CorrectionEscalation,
    run_guardrails_retry,
)
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.system_prompt import CachedLayer, FreshLayer
from ai_sdr.guardrails.validator import GuardrailConfig, ValidationResult


def _td(text: str) -> TurnDecision:
    return TurnDecision(
        response_text=text,
        collected_fields={},
        reasoning="r",
    )


def _ok() -> ValidationResult:
    return ValidationResult(ok=True, violation=None, category=None)


def _violation() -> ValidationResult:
    return ValidationResult(
        ok=False,
        violation="price 'R$ 9999' not in whitelist",
        category="price_invented",
    )


@pytest.mark.asyncio
async def test_first_response_clean_returns_immediately() -> None:
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(side_effect=AssertionError("should not retry"))
    decision = await run_guardrails_retry(
        initial_decision=_td("clean response"),
        initial_validation=_ok(),
        bound_llm=bound_llm,
        cached=CachedLayer(text="C"),
        fresh_builder=lambda _correction: FreshLayer(text="F"),
        inbound_text="oi",
        validator_config=GuardrailConfig(
            disallowed_price_pattern=r"R\$\d+",
            allowed_prices=[],
            allowed_products=[],
            fallback_text="Vou validar com a equipe.",
        ),
    )
    assert decision.response_text == "clean response"
    bound_llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_violation_triggers_one_retry_and_succeeds() -> None:
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(return_value=_td("cleaned response"))
    decision = await run_guardrails_retry(
        initial_decision=_td("R$ 9999 mensal"),
        initial_validation=_violation(),
        bound_llm=bound_llm,
        cached=CachedLayer(text="C"),
        fresh_builder=lambda _correction: FreshLayer(text=f"F + correction:{_correction.category}"),
        inbound_text="quanto custa?",
        validator_config=GuardrailConfig(
            disallowed_price_pattern=r"R\$\d+",
            allowed_prices=[],
            allowed_products=[],
            fallback_text="Vou validar com a equipe.",
        ),
    )
    assert decision.response_text == "cleaned response"
    bound_llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_violation_after_retry_raises_escalation() -> None:
    """If the retry STILL violates, escalation is signaled."""
    bound_llm = AsyncMock()
    bound_llm.ainvoke = AsyncMock(return_value=_td("R$8888 mensal"))
    with pytest.raises(CorrectionEscalation) as exc:
        await run_guardrails_retry(
            initial_decision=_td("R$ 9999 mensal"),
            initial_validation=_violation(),
            bound_llm=bound_llm,
            cached=CachedLayer(text="C"),
            fresh_builder=lambda _correction: FreshLayer(text="F"),
            inbound_text="quanto custa?",
            validator_config=GuardrailConfig(
                disallowed_price_pattern=r"R\$\d+",
                allowed_prices=[],
                allowed_products=[],
                fallback_text="Vou validar com a equipe.",
            ),
        )
    assert "price" in str(exc.value).lower()
