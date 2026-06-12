"""validate_transition new signature: takes a state dict (FE-03a Task 14)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


@dataclass
class _MockState:
    collected: dict
    extracted_facts: dict
    objections_handled: list
    turn_index: int
    active_treatment: dict | None = None


def _treeflow() -> TreeflowDef:
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
        version="1.0.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n, "b": b},
    )


def test_validate_transition_accepts_state_kwarg():
    state = _MockState(
        collected={},
        extracted_facts={},
        objections_handled=[],
        turn_index=1,
    )
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=_treeflow(),
    )
    assert target == "b"
    assert failure is None
