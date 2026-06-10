"""build_fresh_layer renders node-scoped objections (FE-03a Task 13)."""
from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowTransition,
)


def _node_with_objections(objs: list[TreeflowObjection]) -> TreeflowNode:
    return TreeflowNode(
        id="qualificacao",
        objetivo="qualificar",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="qualificacao")],
        handles_objections=objs,
    )


def test_node_scoped_objections_listed():
    objs = [
        TreeflowObjection(
            id="ja_tentei_curso_online",
            description="lead diz que cursos online não funcionam pra ele",
            treatment_mode="tool",
            tool_payload=None,
        )
    ]
    fresh = build_fresh_layer(
        current_node=_node_with_objections(objs),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "NODE-SCOPED OBJECTIONS" in fresh.text
    assert "ja_tentei_curso_online" in fresh.text


def test_no_block_when_node_has_no_objections():
    fresh = build_fresh_layer(
        current_node=_node_with_objections([]),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "NODE-SCOPED OBJECTIONS" not in fresh.text
