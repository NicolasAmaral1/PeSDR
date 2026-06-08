"""validate_transition routes per spec §7."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowCollectField,
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _build_treeflow(
    *,
    node_id: str,
    objetivo: str = "x",
    collects: list[tuple[str, bool]] | None = None,
    exit_type: str = "all_fields_filled",
    exit_expression: str | None = None,
    transitions: list[tuple[str, str]] | None = None,
    extra_nodes: list[str] | None = None,
) -> TreeflowDef:
    collects = collects or []
    transitions = transitions or []
    extra_nodes = extra_nodes or []
    main = TreeflowNode(
        id=node_id,
        objetivo=objetivo,
        bridge_instruction="",
        collects=[
            TreeflowCollectField(field=f, type="text", required=req)
            for f, req in collects
        ],
        exit_condition=TreeflowExitCondition(
            type=exit_type, expression=exit_expression
        ),
        next_nodes=[
            TreeflowTransition(condition=c, target=t) for c, t in transitions
        ],
    )
    nodes = {main.id: main}
    for n in extra_nodes:
        nodes[n] = TreeflowNode(
            id=n, objetivo="x", bridge_instruction="",
            collects=[], exit_condition=TreeflowExitCondition(type="all_fields_filled"),
            next_nodes=[],
        )
    return TreeflowDef(
        id="t", version="1", display_name=None,
        sdr_persona={}, entry_node=node_id, nodes=nodes,
    )


def test_no_suggestion_means_stay() -> None:
    tf = _build_treeflow(node_id="a")
    target, reason = validate_transition(
        current_node="a", next_node_suggestion=None,
        collected={}, treeflow=tf,
    )
    assert target == "a"
    assert reason is None


def test_current_keyword_means_stay() -> None:
    tf = _build_treeflow(node_id="a")
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="current",
        collected={}, treeflow=tf,
    )
    assert target == "a"
    assert reason is None


def test_target_not_in_transitions_is_invalid_target() -> None:
    tf = _build_treeflow(
        node_id="a", transitions=[("true", "b")], extra_nodes=["b", "c"],
    )
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="c",
        collected={}, treeflow=tf,
    )
    assert target == "a"
    assert reason == "invalid_target"


def test_condition_false_blocks_advance() -> None:
    tf = _build_treeflow(
        node_id="a",
        collects=[("segmento", False)],
        transitions=[("segmento == 'saas'", "b")],
        extra_nodes=["b"],
    )
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="b",
        collected={"segmento": "ecommerce"}, treeflow=tf,
    )
    assert target == "a"
    assert reason == "condition_false"


def test_all_fields_filled_with_missing_required_is_exit_not_satisfied() -> None:
    tf = _build_treeflow(
        node_id="a",
        collects=[("segmento", True)],
        transitions=[("true", "b")],
        extra_nodes=["b"],
    )
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="b",
        collected={}, treeflow=tf,
    )
    assert target == "a"
    assert reason == "exit_not_satisfied"


def test_happy_path_advances() -> None:
    tf = _build_treeflow(
        node_id="a",
        collects=[("segmento", True)],
        transitions=[("true", "b")],
        extra_nodes=["b"],
    )
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="b",
        collected={"segmento": "saas"}, treeflow=tf,
    )
    assert target == "b"
    assert reason is None


def test_rule_expression_exit_evaluates() -> None:
    tf = _build_treeflow(
        node_id="a",
        collects=[("ticket", False)],
        exit_type="rule_expression",
        exit_expression="ticket > 1000",
        transitions=[("true", "b")],
        extra_nodes=["b"],
    )
    target, reason = validate_transition(
        current_node="a", next_node_suggestion="b",
        collected={"ticket": 500}, treeflow=tf,
    )
    assert target == "a"
    assert reason == "exit_not_satisfied"

    target2, reason2 = validate_transition(
        current_node="a", next_node_suggestion="b",
        collected={"ticket": 2000}, treeflow=tf,
    )
    assert target2 == "b"
    assert reason2 is None
