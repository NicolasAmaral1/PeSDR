"""build_fresh_layer assembles per-turn dense context."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_sdr.flowengine.system_prompt import (
    CorrectionContext,
    FreshLayer,
    build_fresh_layer,
)
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowCollectField,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _node(
    node_id: str,
    objetivo: str,
    nexts: list[tuple[str, str]] | None = None,
) -> TreeflowNode:
    return TreeflowNode(
        id=node_id,
        objetivo=objetivo,
        bridge_instruction="bridge",
        collects=[TreeflowCollectField(field="segmento", type="text", required=True)],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition=cond, target=t) for cond, t in (nexts or [])],
    )


def test_fresh_layer_includes_current_node_full_detail() -> None:
    current = _node("saudacao", "Cumprimentar e descobrir segmento.", [("true", "qualificacao")])
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime(2026, 6, 2, 14, 32, tzinfo=timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert isinstance(layer, FreshLayer)
    assert "current_node: saudacao" in layer.text
    assert "Cumprimentar e descobrir segmento." in layer.text


def test_fresh_layer_includes_immediate_next_nodes_dense() -> None:
    current = _node("saudacao", "x", [("true", "qualificacao")])
    nxt = _node("qualificacao", "Descobrir ticket medio.", [])
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={"segmento": "saas"},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=2,
        now=datetime(2026, 6, 2, 14, 32, tzinfo=timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "IMMEDIATE NEXT NODES" in layer.text
    assert "qualificacao" in layer.text
    assert "Descobrir ticket medio." in layer.text


def test_fresh_layer_omits_global_map() -> None:
    """No mention of nodes beyond immediate next — per spec decision."""
    current = _node("saudacao", "x", [("true", "qualificacao")])
    nxt = _node("qualificacao", "x", [("true", "demo_offer")])
    # demo_offer is 2 hops away — must NOT appear in fresh layer.
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime(2026, 6, 2, 14, 32, tzinfo=timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "demo_offer" not in layer.text


def test_fresh_layer_includes_time_block() -> None:
    current = _node("saudacao", "x")
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime(2026, 6, 2, 9, 5, tzinfo=timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "HORA ATUAL" in layer.text or "Current time" in layer.text
    assert "2026-06-02" in layer.text


def test_fresh_layer_includes_history_window() -> None:
    current = _node("saudacao", "x")
    history = [
        {
            "role": "user",
            "content": "oi",
            "source": "lead",
            "turn_index": 1,
            "timestamp": "2026-06-02T10:00:00+00:00",
            "media_type": "text",
        },
        {
            "role": "assistant",
            "content": "oi! qual segmento?",
            "source": "agent",
            "turn_index": 1,
            "timestamp": "2026-06-02T10:00:05+00:00",
            "media_type": "text",
        },
    ]
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=history,
        turn_index=2,
        now=datetime(2026, 6, 2, 14, tzinfo=timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="saas",
    )
    assert "RECENT CONVERSATION" in layer.text
    assert "oi! qual segmento?" in layer.text
    assert "saas" in layer.text


def test_fresh_layer_correction_block_when_provided() -> None:
    current = _node("saudacao", "x")
    correction = CorrectionContext(
        previous_response="O investimento e R$2k",
        rejection_reason="mencionou preco antes de qualificar",
        category="premature_transition",
    )
    layer = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
        active_treatment=None,
        correction=correction,
        current_inbound_text="oi",
    )
    assert "CORRECTION" in layer.text or "CORRECAO" in layer.text
    assert "O investimento e R$2k" in layer.text
    assert "premature_transition" in layer.text
