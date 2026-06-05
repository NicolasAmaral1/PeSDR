"""LangChain LLM client wrappers for the FlowEngine main turn call.

Centralizes the structured-output binding for TurnDecision. Resolved
design decision: use method='function_calling' explicitly so behavior
is consistent across Anthropic and OpenAI providers.

`main_llm_for_tenant` is the production entrypoint. Tests inject the
underlying chat model directly via `build_structured_llm` to avoid
provider auth in unit tests.
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from ai_sdr.flowengine.decision import TurnDecision


def build_structured_llm(chat_model: BaseChatModel | Any) -> Runnable:
    """Bind TurnDecision as the structured-output schema.

    Pure function: takes any chat model + returns the bound runnable.
    Kept separate from main_llm_for_tenant so tests can inject fakes.
    """
    return chat_model.with_structured_output(TurnDecision, method="function_calling")


def main_llm_for_tenant(llm_cfg: Any) -> Runnable:
    """Build the structured TurnDecision LLM from a tenant.llm.default config.

    Expected fields on llm_cfg (from existing TenantLlmConfig):
      - provider: "anthropic" | "openai" | "google" | ...
      - model: model name string
      - api_key: resolved secret string
      - (optional) temperature, max_tokens, timeout
    """
    chat = init_chat_model(
        model=llm_cfg.model,
        model_provider=llm_cfg.provider,
        api_key=llm_cfg.api_key,
        temperature=getattr(llm_cfg, "temperature", 0.7),
    )
    return build_structured_llm(chat)
