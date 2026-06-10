"""Tests for run_with_guardrails — retry loop, fallback, telemetry hooks."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel

from ai_sdr.guardrails.runner import (
    ExtractResultProto,
    GuardrailsRunResult,
    run_with_guardrails,
)
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


class _Result(BaseModel):
    """Minimal ExtractResult-shaped object: required by ExtractResultProto."""

    response_text: str
    prices_mentioned: list[int] = []
    products_mentioned: list[str] = []
    collected: dict[str, Any] = {}


def _gr(prices: list[int], products: list[str], max_retries: int = 2) -> GuardrailsConfig:
    # FE-03a Task 10: allowed_products must be non-empty when enabled. Tests
    # that exercise the prices-only whitelist still need a placeholder product
    # so the schema validates; the runner logic under test is unaffected.
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=prices,
        allowed_products=products or ["Mentoria"],
        fallback_text="Confirmo já já, ok?",
        max_retries=max_retries,
    )


def _llm_defaults_no_classifier() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(provider="anthropic", model="x", api_key_ref="secrets/anthropic_key")
    )


async def test_passes_on_first_attempt_when_clean() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247], collected={"a": 1})

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert isinstance(res, GuardrailsRunResult)
    assert res.blocked is False
    assert res.attempts == 0
    assert res.response_text == "ok"
    assert res.collected == {"a": 1}


async def test_retries_with_feedback_until_clean() -> None:
    calls: list[list[BaseMessage]] = []
    responses = [
        _Result(response_text="bad1", prices_mentioned=[5000]),
        _Result(response_text="bad2", prices_mentioned=[9999]),
        _Result(response_text="good", prices_mentioned=[247], collected={"x": 1}),
    ]

    async def inner(messages: list[BaseMessage]) -> _Result:
        calls.append(list(messages))
        return responses.pop(0)

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is False
    assert res.attempts == 2
    assert res.response_text == "good"
    # each retry appended a fix message
    assert any(
        "não autorizado" in str(m.content).lower() for m in calls[1] if hasattr(m, "content")
    )


async def test_falls_back_when_retries_exhausted() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="bad", prices_mentioned=[9999])

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], [], max_retries=2),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is True
    assert res.attempts == 3  # 1 initial + 2 retries
    assert res.response_text == "Confirmo já já, ok?"
    assert res.collected == {}


async def test_guardrails_disabled_is_pure_passthrough() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="anything", prices_mentioned=[9999])

    gr_off = GuardrailsConfig(
        enabled=False,
        allowed_prices=[],
        allowed_products=[],
        fallback_text="Confirmo já já, ok?",
    )

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=gr_off,
        critical=True,  # ignored when guardrails disabled
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is False
    assert res.attempts == 0
    assert res.response_text == "anything"


async def test_critic_pass_invoked_when_critical_and_critic_enabled() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247])

    critic_calls: list[str] = []

    async def fake_critic(*_a: Any, response_text: str, **_kw: Any) -> Verdict:
        critic_calls.append(response_text)
        return Verdict(passed=True)

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=True,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
        critic_pass_fn=fake_critic,
    )
    assert critic_calls == ["ok"]
    assert res.blocked is False


async def test_critic_skipped_when_critic_enabled_false() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247])

    critic_calls: list[str] = []

    async def fake_critic(*_a: Any, **_kw: Any) -> Verdict:
        critic_calls.append("called")
        return Verdict(passed=True)

    gr = _gr([247], [])
    gr_no_critic = gr.model_copy(update={"critic_enabled": False})

    await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=gr_no_critic,
        critical=True,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
        critic_pass_fn=fake_critic,
    )
    assert critic_calls == []


# Silence unused-import lint for ExtractResultProto (re-exported for callers).
_ = ExtractResultProto
