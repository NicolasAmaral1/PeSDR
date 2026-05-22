import pytest

from ai_sdr.llm.factory import (
    LLMSecretNotFoundError,
    UnknownProviderError,
    build_llm,
    resolve_api_key,
)
from ai_sdr.schemas.llm_yaml import LLMConfig


def test_resolve_api_key_reads_from_secrets_dict() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
    secrets = {"anthropic_key": "sk-ant-xxx"}
    assert resolve_api_key(cfg, secrets) == "sk-ant-xxx"


def test_resolve_api_key_raises_when_missing() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
    with pytest.raises(LLMSecretNotFoundError, match="anthropic_key"):
        resolve_api_key(cfg, {})


def test_build_llm_anthropic_returns_chat_anthropic_instance() -> None:
    from langchain_anthropic import ChatAnthropic

    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        temperature=0.5,
        api_key_ref="secrets/anthropic_key",
    )
    llm = build_llm(cfg, secrets={"anthropic_key": "sk-ant-test"})
    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-sonnet-4-6"
    assert llm.temperature == 0.5


def test_build_llm_openai_returns_chat_openai_instance() -> None:
    from langchain_openai import ChatOpenAI

    cfg = LLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.3,
        api_key_ref="secrets/openai_key",
    )
    llm = build_llm(cfg, secrets={"openai_key": "sk-openai-test"})
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gpt-4o-mini"


def test_build_llm_unknown_provider_raises() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        model="x",
        api_key_ref="secrets/anthropic_key",
    )
    object.__setattr__(cfg, "provider", "wat")
    with pytest.raises(UnknownProviderError):
        build_llm(cfg, secrets={"anthropic_key": "x"})
