"""close_lifecycle.evaluate_completion_rule pure function (FE-03b Task 11)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdr.flowengine.close_lifecycle import CloseOutcome, evaluate_completion_rule
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowCompletionRule,
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTalkLifecycle,
    TreeflowTransition,
)


@dataclass
class _State:
    collected: dict = field(default_factory=dict)
    extracted_facts: dict = field(default_factory=dict)
    turn_index: int = 1


def _treeflow_with_rules(rules: list[TreeflowCompletionRule]) -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n},
        talk_lifecycle=TreeflowTalkLifecycle(close_when_completed=rules),
    )


def _decision(**collected) -> TurnDecision:
    return TurnDecision(
        response_text="x",
        collected_fields=collected,
        reasoning="r",
    )


def test_returns_none_when_no_lifecycle():
    tf = _treeflow_with_rules([])
    tf.talk_lifecycle = None
    state = _State()
    decision = _decision(demo_agendada=True)
    assert evaluate_completion_rule(state=state, decision=decision, treeflow=tf) is None


def test_returns_none_when_lifecycle_has_no_rules():
    tf = _treeflow_with_rules([])
    state = _State()
    decision = _decision(demo_agendada=True)
    assert evaluate_completion_rule(state=state, decision=decision, treeflow=tf) is None


def test_returns_outcome_when_success_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.demo_agendada == True",
        outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(demo_agendada=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is not None
    assert out.status == "closed_completed_success"
    assert "demo_agendada" in out.reason
    assert out.closed_by == "pipeline_hook"


def test_returns_outcome_when_failure_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.lost == True",
        outcome="failure",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(lost=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_completed_failure"


def test_returns_outcome_when_no_interest_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.no_interest_flag == True",
        outcome="no_interest",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(no_interest_flag=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_no_interest"


def test_first_matching_rule_wins():
    rules = [
        TreeflowCompletionRule(
            expression="collected.first == True",
            outcome="failure",
        ),
        TreeflowCompletionRule(
            expression="collected.first == True",
            outcome="success",  # never reached
        ),
    ]
    tf = _treeflow_with_rules(rules)
    state = _State()
    decision = _decision(first=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_completed_failure"


def test_rule_seeing_only_state_collected():
    """state.collected is in scope (not just decision.collected_fields)."""
    rule = TreeflowCompletionRule(
        expression="collected.flag == True",
        outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State(collected={"flag": True})
    decision = _decision()
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is not None


def test_runtime_exception_in_rule_is_swallowed():
    """If simpleeval raises (unbound name, etc.) at runtime, skip the rule."""
    rule = TreeflowCompletionRule(
        expression="nonexistent_name > 0",
        outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision()
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is None
