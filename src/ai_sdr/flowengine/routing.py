"""Transition validation for the FlowEngine.

Pure function per spec §7. Decides whether the LLM's next_node_suggestion
is a valid advance from the current node, given the runtime state and
the TreeFlow definition.

Returns (resolved_target_node_id, failure_reason). failure_reason is None
on success; on failure the target stays at current_node and the reason
is one of: invalid_target | condition_false | transition_blocked_by_treatment
| exit_not_satisfied.

The orchestrator (Task 17) uses the failure reason to drive corrective
retries via run_transition_retry (Task 13).
"""

from __future__ import annotations

from typing import Any, Protocol

from simpleeval import SimpleEval

from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
)


class _StateProto(Protocol):
    collected: dict[str, Any]
    extracted_facts: dict[str, Any]
    objections_handled: list[Any]
    turn_index: int
    active_treatment: Any  # ActiveTreatment | None — typed Any to avoid import cycle


def validate_transition(
    *,
    current_node: str,
    next_node_suggestion: str | None,
    state: _StateProto,
    treeflow: TreeflowDef,
) -> tuple[str, str | None]:
    """Validate a transition. See module docstring."""
    if next_node_suggestion is None or next_node_suggestion in ("current", current_node):
        return current_node, None

    node = treeflow.nodes.get(current_node)
    if node is None:
        return current_node, "invalid_target"

    matching = [t for t in node.next_nodes if t.target == next_node_suggestion]
    if not matching:
        return current_node, "invalid_target"

    transition = matching[0]
    if transition.condition.strip() != "true":
        if not _eval_bool(transition.condition, state):
            return current_node, "condition_false"

    # Brecha C2 (FE-03a Task 16): block transitions while a treatment is in
    # progress. The system prompt instructs the LLM not to propose transitions
    # during active_treatment, but routing must enforce it to keep state
    # consistent if the LLM disobeys. The failure_reason rides the existing
    # run_transition_retry loop so the LLM regenerates without a transition.
    if state.active_treatment is not None:
        return current_node, "transition_blocked_by_treatment"

    if not _exit_satisfied(node, state.collected):
        return current_node, "exit_not_satisfied"

    return next_node_suggestion, None


def _exit_satisfied(node: TreeflowNode, collected: dict[str, Any]) -> bool:
    ec: TreeflowExitCondition = node.exit_condition
    if ec.type == "all_fields_filled":
        for c in node.collects:
            if c.required and collected.get(c.field) in (None, ""):
                return False
        return True
    if ec.type == "rule_expression":
        return _eval_bool_collected(ec.expression or "false", collected)
    if ec.type == "combined":
        for c in node.collects:
            if c.required and collected.get(c.field) in (None, ""):
                return False
        return _eval_bool_collected(ec.expression or "false", collected)
    if ec.type == "llm_judge":
        # Reserved for FE-03+. In FE-01b, default to "not satisfied" so the
        # LLM is nudged to stay (matches the conservative spec §11.2).
        return False
    return False


def _eval_bool(expression: str, state: _StateProto) -> bool:
    """Evaluate a simpleeval expression against an extended context.

    Names available in the expression (brecha C1, FE-03a §8.1):
      - top-level collected field names (retrocompat with v1 YAML)
      - collected: dict of all collected fields
      - extracted_facts: dict of facts
      - objections_handled: list of {id, resolution} dicts
      - turn_index: int
    """
    context: dict[str, Any] = dict(state.collected)
    context["collected"] = state.collected
    context["extracted_facts"] = state.extracted_facts
    context["objections_handled"] = [
        {
            "id": getattr(o, "objection_id", None) or o.get("objection_id"),
            "resolution": getattr(o, "resolution", None) or o.get("resolution"),
        }
        for o in state.objections_handled
    ]
    context["turn_index"] = state.turn_index
    try:
        return bool(SimpleEval(names=context, functions={"len": len}).eval(expression))
    except Exception:
        return False


def _eval_bool_collected(expression: str, collected: dict[str, Any]) -> bool:
    try:
        return bool(SimpleEval(names=collected).eval(expression))
    except Exception:
        return False
