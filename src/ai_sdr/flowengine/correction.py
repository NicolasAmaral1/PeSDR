"""Corrective retry orchestration for FlowEngine v2.

Two retry helpers:
  - run_guardrails_retry (Task 10): on validator violation, rebuild fresh
    layer with CorrectionContext, re-invoke LLM. Max 1 retry. After 2
    violations, raise CorrectionEscalation -> orchestrator escalates Talk.
  - run_transition_retry (Task 13): on invalid transition, similar pattern.

Both helpers are PURE (no DB writes). The orchestrator (Task 17) handles
state mutations and escalation persistence.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.runnables import Runnable

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.system_prompt import (
    CachedLayer,
    CorrectionContext,
    FreshLayer,
    assemble_prompt,
)
from ai_sdr.guardrails.validator import (
    GuardrailConfig,
    ValidationResult,
    validate_response_text,
)


class CorrectionEscalation(Exception):
    """Raised when a corrective retry still fails — caller escalates Talk."""


async def run_guardrails_retry(
    *,
    initial_decision: TurnDecision,
    initial_validation: ValidationResult,
    bound_llm: Runnable,
    cached: CachedLayer,
    fresh_builder: Callable[[CorrectionContext], FreshLayer],
    inbound_text: str,
    validator_config: GuardrailConfig,
) -> TurnDecision:
    """Return a TurnDecision that has passed guardrails (after at most 1 retry).

    Args:
      initial_decision: the result of the main LLM call.
      initial_validation: result of running the guardrails on it.
      bound_llm: the structured-output LLM (TurnDecision schema bound).
      cached: the cached prompt layer (unchanged across retries).
      fresh_builder: callable that produces a NEW FreshLayer given a
        CorrectionContext. The orchestrator passes a closure that captures
        the rest of the state.
      inbound_text: the lead's inbound text (unchanged across retries).
      validator_config: the guardrails to re-check after the retry.

    Raises:
      CorrectionEscalation: when the retry response ALSO violates.
    """
    if initial_validation.ok:
        return initial_decision

    correction = CorrectionContext(
        previous_response=initial_decision.response_text,
        rejection_reason=initial_validation.violation or "guardrails violation",
        category=initial_validation.category or "guardrails_violation",
    )
    new_fresh = fresh_builder(correction)
    messages = assemble_prompt(cached, new_fresh, inbound_text=inbound_text)
    retry_decision: TurnDecision = await bound_llm.ainvoke(messages)
    retry_validation = validate_response_text(
        retry_decision.response_text, validator_config
    )
    if retry_validation.ok:
        return retry_decision

    raise CorrectionEscalation(
        f"guardrails retry failed: {retry_validation.violation}"
    )


async def run_transition_retry(
    *,
    initial_decision: TurnDecision,
    initial_target: str,
    initial_failure: str | None,
    bound_llm: Runnable,
    cached: CachedLayer,
    fresh_builder: Callable[[CorrectionContext], FreshLayer],
    inbound_text: str,
    revalidate: Callable[[TurnDecision], tuple[str, str | None]],
    current_node: str,
) -> tuple[TurnDecision, str]:
    """One corrective retry on invalid transition. Returns (decision, target).

    Falls back to (original decision, current_node) if the retry also fails.
    Unlike run_guardrails_retry, this does NOT raise — invalid routing is a
    soft failure: the original response_text is still sent to the lead.
    """
    if initial_failure is None:
        return initial_decision, initial_target

    correction = CorrectionContext(
        previous_response=(
            f"suggested transition to {initial_decision.next_node_suggestion!r}"
        ),
        rejection_reason=(
            f"transition failed: {initial_failure}. Reconsider: either complete "
            "the missing collection or do not advance."
        ),
        category=initial_failure,
    )
    new_fresh = fresh_builder(correction)
    messages = assemble_prompt(cached, new_fresh, inbound_text=inbound_text)
    retry_decision: TurnDecision = await bound_llm.ainvoke(messages)

    retry_target, retry_failure = revalidate(retry_decision)
    if retry_failure is None:
        return retry_decision, retry_target

    # Soft fallback: keep original response_text, stay in current_node.
    return initial_decision, current_node
