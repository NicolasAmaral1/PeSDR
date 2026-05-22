"""Build a `BaseChatModel` from an `LLMConfig` + tenant secrets."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from ai_sdr.schemas.llm_yaml import LLMConfig


class LLMSecretNotFoundError(KeyError):
    """The secret referenced by `api_key_ref` is not in the secrets dict."""


class UnknownProviderError(ValueError):
    """The provider in LLMConfig is not registered."""


def resolve_api_key(cfg: LLMConfig, secrets: dict[str, str]) -> str:
    """`api_key_ref` is 'secrets/<name>'; return secrets[<name>]."""
    name = cfg.api_key_ref.removeprefix("secrets/")
    if name not in secrets:
        raise LLMSecretNotFoundError(name)
    return secrets[name]


def build_llm(cfg: LLMConfig, secrets: dict[str, str]) -> BaseChatModel:
    """Instantiate a LangChain chat model based on `cfg.provider`."""
    api_key = resolve_api_key(cfg, secrets)
    kwargs: dict[str, object] = {"temperature": cfg.temperature}
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens

    if cfg.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=cfg.model, api_key=api_key, **kwargs)

    if cfg.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=cfg.model, api_key=api_key, **kwargs)

    raise UnknownProviderError(f"unsupported provider: {cfg.provider!r}")
