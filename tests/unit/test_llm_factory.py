"""Tests for build_llm — provider-agnostic via init_chat_model."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_sdr.llm.factory import build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig


def _cfg(
    provider: str,
    model: str = "m",
    api_key_ref: str = "secrets/k",
) -> LLMConfig:
    return LLMConfig(provider=provider, model=model, api_key_ref=api_key_ref, temperature=0.5)


def test_anthropic_dispatched_via_init_chat_model() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("anthropic", "claude-sonnet-4-6", "secrets/anthropic_key"),
            secrets={"secrets/anthropic_key": "sk-fake"},
        )
    fake.assert_called_once()
    args, kwargs = fake.call_args
    assert args[0] == "anthropic:claude-sonnet-4-6"
    assert kwargs["api_key"] == "sk-fake"
    assert kwargs["temperature"] == 0.5


def test_openai_dispatched_via_init_chat_model() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("openai", "gpt-4o", "secrets/openai_key"),
            secrets={"secrets/openai_key": "sk-openai-fake"},
        )
    args, kwargs = fake.call_args
    assert args[0] == "openai:gpt-4o"
    assert kwargs["api_key"] == "sk-openai-fake"


def test_google_genai_dispatched() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("google_genai", "gemini-2.0-flash", "secrets/google_key"),
            secrets={"secrets/google_key": "AIza-fake"},
        )
    args, kwargs = fake.call_args
    assert args[0] == "google_genai:gemini-2.0-flash"
    assert kwargs["api_key"] == "AIza-fake"


def test_deepseek_dispatched() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("deepseek", "deepseek-chat", "secrets/deepseek_key"),
            secrets={"secrets/deepseek_key": "sk-ds-fake"},
        )
    args, kwargs = fake.call_args
    assert args[0] == "deepseek:deepseek-chat"
    assert kwargs["api_key"] == "sk-ds-fake"


def test_ollama_dispatched_without_api_key() -> None:
    """Ollama is local — no api_key. Factory should still work."""
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("ollama", "llama3.2", "secrets/ollama_key"),
            secrets={"secrets/ollama_key": ""},  # empty / unused
        )
    args, kwargs = fake.call_args
    assert args[0] == "ollama:llama3.2"


def test_missing_api_key_in_secrets_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="secrets/anthropic_key"):
        build_llm(
            _cfg("anthropic", "m", "secrets/anthropic_key"),
            secrets={},
        )


def test_arbitrary_provider_string_accepted_by_schema() -> None:
    """Schema is free-form now; factory delegates to init_chat_model."""
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(
            _cfg("brand_new_provider", "some-model", "secrets/x"),
            secrets={"secrets/x": "y"},
        )
    args, _ = fake.call_args
    assert args[0] == "brand_new_provider:some-model"
