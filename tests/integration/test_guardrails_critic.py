"""Tests for critic_pass — uses a FakeStructuredLLM, no live LLM call."""

from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda

from ai_sdr.guardrails.critic import critic_pass
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message

pytestmark = pytest.mark.integration


class _FakeLLM:
    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.last_messages: list = []

    def with_structured_output(self, model: type) -> RunnableLambda:
        async def _run(messages: list) -> Verdict:
            self.last_messages = messages
            return self._verdict

        return RunnableLambda(_run)


def _llm_defaults() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        ),
        classifier=LLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key_ref="secrets/anthropic_key",
        ),
    )


def _guardrails() -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=[247, 1497, 6000],
        allowed_products=["Mentoria", "Aceleradora"],
        fallback_text="Confirmo já já, ok?",
    )


async def test_critic_passes_clean_response() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005

    v = await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="A Mentoria custa R$ 6000 e tem 7 dias de garantia.",
        kb_chunks=[
            RetrievedChunk(content="Mentoria 6000", heading_path="Preços", kb_id="kb_x", score=0.9)
        ],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert v.passed is True


async def test_critic_flags_bad_response() -> None:
    fake = _FakeLLM(
        Verdict(passed=False, reason="mentioned R$ 9999", suggested_fix="refaça sem 9999")
    )
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005

    v = await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="A Mentoria custa R$ 9999.",
        kb_chunks=[],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert v.passed is False
    assert "9999" in v.reason  # type: ignore[operator]


async def test_critic_uses_classifier_llm_not_default() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    captured: dict = {}

    def factory(cfg: LLMConfig, secrets: dict[str, str], node_id: str) -> _FakeLLM:
        captured["model"] = cfg.model
        return fake

    await critic_pass(
        llm_factory=factory,  # type: ignore[arg-type]
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="ok",
        kb_chunks=[],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert captured["model"] == "claude-haiku-4-5"


async def test_critic_prompt_contains_kb_chunks_and_history() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005
    history: list[Message] = [
        {"role": "user", "content": "tem desconto?"},
        {"role": "assistant", "content": "não trabalhamos com desconto"},
    ]
    await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="proposta",
        kb_chunks=[
            RetrievedChunk(content="KB-FACT-123", heading_path="X", kb_id="kb_x", score=0.9)
        ],
        recent_history=history,
        guardrails=_guardrails(),
    )
    # System message (or first message) must contain the rendered KB + history hints
    blob = " ".join(
        m.content if isinstance(m.content, str) else str(m.content) for m in fake.last_messages
    )
    assert "KB-FACT-123" in blob
    assert "desconto" in blob


async def test_critic_raises_if_no_classifier_configured() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005
    cfg = LLMDefaults(
        default=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        )
    )  # no classifier

    with pytest.raises(ValueError, match="classifier"):
        await critic_pass(
            llm_factory=factory,
            tenant_llm=cfg,
            secrets={"anthropic_key": "sk-fake"},
            response_text="ok",
            kb_chunks=[],
            recent_history=[],
            guardrails=_guardrails(),
        )
