"""LLM factory — provider-agnostic dispatch via langchain.chat_models.init_chat_model.

Plan 3 T2b opened this from a 2-provider if/else (anthropic, openai) to free-form.
Supported providers are whichever langchain-<x> packages are installed; the
factory does not validate the provider name (init_chat_model raises if it can't
resolve the package).

End-to-end validation of providers beyond anthropic + openai is Plan 4's job.
"""

from __future__ import annotations

from typing import Any, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ai_sdr.schemas.llm_yaml import LLMConfig


def build_llm(cfg: LLMConfig, secrets: dict[str, str]) -> BaseChatModel:
    """Build a chat model. Caller passes the secrets dict; we resolve api_key_ref.

    The `secrets/` prefix on `api_key_ref` is a documentation convention
    ("this is a SOPS secret reference, not a plaintext key"); the actual lookup
    key in the secrets dict is the bare name (e.g. `"anthropic_key"`), which is
    how `SopsLoader` shapes its output. A missing key raises `KeyError`.
    """
    api_key = secrets[cfg.api_key_ref.removeprefix("secrets/")]
    kwargs: dict[str, Any] = {"api_key": api_key}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return cast(BaseChatModel, init_chat_model(f"{cfg.provider}:{cfg.model}", **kwargs))
