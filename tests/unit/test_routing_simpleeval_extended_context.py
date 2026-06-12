"""Extended simpleeval context (FE-03a Task 15, brecha C1).

Names: extracted_facts, objections_handled, turn_index.
"""

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


def _tf(condition: str) -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition=condition, target="b")],
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


def test_condition_can_reference_extracted_facts():
    tf = _tf("extracted_facts.dor_principal == 'tempo'")
    state = _State(extracted_facts={"dor_principal": "tempo"})
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_turn_index():
    tf = _tf("turn_index >= 5")
    state = _State(turn_index=6)
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_collected_topmost_legacy():
    """Retrocompat: condition referring to a collected field by bare name still works."""
    tf = _tf("ticket_medio >= 50000")
    state = _State(collected={"ticket_medio": 60000})
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_objections_handled_length():
    tf = _tf("len(objections_handled) > 0")
    state = _State(objections_handled=[{"id": "preco", "resolution": "deferred"}])
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=tf,
    )
    # simpleeval supports len() on lists.
    assert target == "b" and failure is None
