"""Talk close evaluation — pure function (FE-03b §5.3).

Called from post_processing.apply_decision after state delta application.
Returns a CloseOutcome if a completion rule fires; None otherwise.

The worker scan job (scan_talks.py) handles inactivity + duration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from simpleeval import SimpleEval

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import TreeflowDef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloseOutcome:
    """Returned by evaluate_completion_rule when a rule fires."""

    status: str
    """One of: closed_completed_success | closed_completed_failure | closed_no_interest."""

    reason: str
    """Human-readable explanation (e.g., 'completion_rule: collected.X == True')."""

    closed_by: str
    """Always 'pipeline_hook' for this code path."""


def evaluate_completion_rule(
    *,
    state: Any,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> CloseOutcome | None:
    """Check if any close_when_completed rule fires against state+decision.

    The first matching rule wins. Runtime evaluation errors (unbound names,
    type errors) are swallowed and the rule is skipped — the loader is
    responsible for catching syntax errors at parse time.
    """
    lifecycle = treeflow.talk_lifecycle
    if lifecycle is None or not lifecycle.close_when_completed:
        return None

    merged_collected = {**_get(state, "collected", {}), **decision.collected_fields}
    context: dict[str, Any] = {
        **merged_collected,
        "collected": merged_collected,
        "extracted_facts": _get(state, "extracted_facts", {}),
        "turn_index": _get(state, "turn_index", 0),
    }

    for rule in lifecycle.close_when_completed:
        try:
            if bool(SimpleEval(names=context).eval(rule.expression)):
                return CloseOutcome(
                    status=_outcome_to_status(rule.outcome),
                    reason=f"completion_rule: {rule.expression}",
                    closed_by="pipeline_hook",
                )
        except Exception as exc:
            logger.info(
                "close_lifecycle.rule_eval_skipped expression=%s err=%s",
                rule.expression,
                exc,
            )
            continue

    return None


def _get(state: Any, key: str, default: Any) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def _outcome_to_status(outcome: str) -> str:
    if outcome == "success":
        return "closed_completed_success"
    if outcome == "failure":
        return "closed_completed_failure"
    if outcome == "no_interest":
        return "closed_no_interest"
    raise ValueError(f"unknown outcome: {outcome!r}")
