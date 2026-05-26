"""run_with_guardrails — retry loop coordinating whitelist + critic + fallback.

The `_handle_exhausted` hook is intentionally factored out so a future HITL plan
can replace it with `await persist_pending_review(...); raise GraphInterrupt()`
without touching the retry loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from langchain_core.messages import BaseMessage, SystemMessage

from ai_sdr.guardrails.critic import critic_pass as _default_critic_pass
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.guardrails.whitelist import validate_whitelist
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message

logger = structlog.get_logger(__name__)


class ExtractResultProto(Protocol):
    """Minimal contract for the object returned by `inner` — same shape as the
    Pydantic model produced by build_structured_model() when guardrails are active."""

    response_text: str
    prices_mentioned: list[int]
    products_mentioned: list[str]


@dataclass
class GuardrailsRunResult:
    response_text: str
    collected: dict[str, Any]
    blocked: bool
    attempts: int


CriticPassFn = Callable[..., Awaitable[Verdict]]


_RESERVED_FIELDS = {"response_text", "prices_mentioned", "products_mentioned"}


def _collected_from_result(result: ExtractResultProto, reserved: set[str]) -> dict[str, Any]:
    """Extract everything except response_text and the mention fields into a dict.

    If the model exposes a nested ``collected`` dict, merge its contents in
    (supports both flat models from build_structured_model and simpler shapes
    that aggregate collects under a single ``collected`` field)."""
    out: dict[str, Any] = {}
    if hasattr(result, "model_dump"):
        dumped = result.model_dump()
        for k, v in dumped.items():
            if k in reserved:
                continue
            if v is None:
                continue
            if k == "collected" and isinstance(v, dict):
                out.update(v)
                continue
            out[k] = v
    return out


async def run_with_guardrails(
    *,
    inner: Callable[[list[BaseMessage]], Awaitable[ExtractResultProto]],
    base_messages: list[BaseMessage],
    guardrails: GuardrailsConfig | None,
    critical: bool,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    llm_factory: Any,
    critic_pass_fn: CriticPassFn | None = None,
) -> GuardrailsRunResult:
    """Run inner, validate, retry with feedback, fallback if exhausted."""
    cp_fn = critic_pass_fn or _default_critic_pass
    guardrails_active = guardrails is not None and guardrails.enabled
    critic_active = (
        critical and guardrails_active and guardrails is not None and guardrails.critic_enabled
    )
    max_retries = guardrails.max_retries if guardrails is not None else 2

    messages = list(base_messages)
    attempt = 0
    last_verdict: Verdict | None = None

    while attempt <= max_retries:
        result = await inner(messages)

        if guardrails_active and guardrails is not None:
            v = validate_whitelist(
                prices_mentioned=getattr(result, "prices_mentioned", []) or [],
                products_mentioned=getattr(result, "products_mentioned", []) or [],
                guardrails=guardrails,
            )
            if not v.passed:
                logger.info("guardrail.blocked", attempt=attempt, reason=v.reason)
                last_verdict = v
                if attempt == max_retries:
                    return _handle_exhausted(guardrails, last_verdict, max_retries)
                messages = list(base_messages) + [
                    SystemMessage(content=v.suggested_fix or "Refaça respeitando a whitelist.")
                ]
                attempt += 1
                continue

        if critic_active and guardrails is not None:
            v_c = await cp_fn(
                llm_factory=llm_factory,
                tenant_llm=tenant_llm,
                secrets=secrets,
                response_text=result.response_text,
                kb_chunks=kb_chunks,
                recent_history=recent_history,
                guardrails=guardrails,
            )
            if not v_c.passed:
                logger.info("critic.flagged", attempt=attempt, reason=v_c.reason)
                last_verdict = v_c
                if attempt == max_retries:
                    return _handle_exhausted(guardrails, last_verdict, max_retries)
                messages = list(base_messages) + [
                    SystemMessage(content=v_c.suggested_fix or "Refaça com base no critic.")
                ]
                attempt += 1
                continue

        return GuardrailsRunResult(
            response_text=result.response_text,
            collected=_collected_from_result(result, _RESERVED_FIELDS),
            blocked=False,
            attempts=attempt,
        )

    # Should be unreachable given the in-loop returns above, but keep defensive
    return _handle_exhausted(guardrails, last_verdict, max_retries)


def _handle_exhausted(
    guardrails: GuardrailsConfig | None,
    last_verdict: Verdict | None,
    max_retries: int,
) -> GuardrailsRunResult:
    """Fallback hook. Future HITL plan can replace this body with:
        await persist_pending_review(...); raise GraphInterrupt()
    without touching the retry loop."""
    reason = last_verdict.reason if last_verdict is not None else "unknown"
    logger.info("guardrail.fallback_used", reason=reason)
    fallback = (
        guardrails.fallback_text
        if guardrails is not None
        else "Deixa eu confirmar e já te respondo."
    )
    return GuardrailsRunResult(
        response_text=fallback,
        collected={},
        blocked=True,
        attempts=max_retries + 1,
    )
