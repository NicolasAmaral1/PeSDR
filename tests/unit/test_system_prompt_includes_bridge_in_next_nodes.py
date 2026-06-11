"""IMMEDIATE NEXT NODES block includes bridge_instruction (FE-03a Task 12, brecha C3)."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _node(node_id: str, bridge: str = "") -> TreeflowNode:
    return TreeflowNode(
        id=node_id,
        objetivo=f"objetivo de {node_id}",
        bridge_instruction=bridge,
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target=node_id)],
    )


def test_next_node_bridge_instruction_rendered():
    current = _node("qualificacao", bridge="ignore for current")
    nxt = _node("oferta_mentoria", bridge="Mencione que ROI cabe em 1 mês")
    fresh = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="ok",
    )
    assert "Mencione que ROI cabe em 1 mês" in fresh.text


def test_block_mentions_compound_response_permission():
    current = _node("a")
    nxt = _node("b", bridge="bridge b")
    fresh = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="ok",
    )
    text_lower = fresh.text.lower()
    assert "bridge_instruction" in text_lower
    assert "within the same response" in text_lower or "no mesmo response" in text_lower
