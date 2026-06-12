"""Fresh layer renders ACTIVE TREATMENT block with conservative guidance (FE-03a Task 11)."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _node(node_id: str = "n") -> TreeflowNode:
    return TreeflowNode(
        id=node_id,
        objetivo="x",
        bridge_instruction="bridge",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target=node_id)],
    )


def test_no_active_treatment_block_when_state_is_none():
    fresh = build_fresh_layer(
        current_node=_node(),
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
    assert "ACTIVE TREATMENT" not in fresh.text


def test_active_treatment_block_shown_when_set():
    at = {
        "objection_id": "preco",
        "current_treatment_turn": 2,
        "max_treatment_turns": 3,
        "resolution_criteria": "lead aceitou parcelamento",
        "treatment_history": ["argumentou ROI"],
    }
    fresh = build_fresh_layer(
        current_node=_node(),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=2,
        now=datetime.now(timezone.utc),
        active_treatment=at,
        correction=None,
        current_inbound_text="ainda tá caro",
    )
    assert "ACTIVE TREATMENT" in fresh.text
    assert "preco" in fresh.text
    assert "turn 2 of 3" in fresh.text
    assert "lead aceitou parcelamento" in fresh.text


def test_active_treatment_block_includes_conservative_resolution_guidance():
    """Conservative resolution: prefer deferred over accepted when ambiguous."""
    at = {
        "objection_id": "preco",
        "current_treatment_turn": 1,
        "max_treatment_turns": 3,
        "resolution_criteria": "lead aceitou parcelamento",
        "treatment_history": [],
    }
    fresh = build_fresh_layer(
        current_node=_node(),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=at,
        correction=None,
        current_inbound_text="ok",
    )
    assert "prefira" in fresh.text.lower()
    assert "deferred" in fresh.text.lower()
