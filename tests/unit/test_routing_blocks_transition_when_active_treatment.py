"""Routing blocks transitions while active_treatment is set (FE-03a Task 16, brecha C2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


@dataclass
class _State:
    collected: dict = field(default_factory=dict)
    extracted_facts: dict = field(default_factory=dict)
    objections_handled: list = field(default_factory=list)
    turn_index: int = 1
    active_treatment: object | None = None


def _tf() -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    b = TreeflowNode(
        id="b",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    return TreeflowDef(
        id="t",
        version="1.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n, "b": b},
    )


def test_transition_blocked_during_active_treatment():
    state = _State(active_treatment={"objection_id": "preco"})
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=_tf(),
    )
    assert target == "a"
    assert failure == "transition_blocked_by_treatment"


def test_transition_allowed_when_treatment_is_none():
    state = _State(active_treatment=None)
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=_tf(),
    )
    assert target == "b"
    assert failure is None


def test_staying_in_same_node_allowed_even_during_treatment():
    state = _State(active_treatment={"objection_id": "preco"})
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="a",
        state=state,
        treeflow=_tf(),
    )
    assert target == "a"
    assert failure is None
