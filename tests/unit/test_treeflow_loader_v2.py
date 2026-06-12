"""TreeflowLoader v2 parses persona + entry + minimal node structure."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowNode,
    TreeflowLoadError,
    load_treeflow_v2,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "avelum_treeflow_v2_minimal.yaml"


def test_loads_minimal_treeflow() -> None:
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert isinstance(tf, TreeflowDef)
    assert tf.id == "avelum_minimal"
    assert tf.entry_node == "saudacao"
    assert "Tom PT-BR informal" in tf.sdr_persona["voice"]


def test_loaded_nodes_are_indexed_by_id() -> None:
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert set(tf.nodes.keys()) == {"saudacao", "qualificacao"}
    node = tf.nodes["saudacao"]
    assert isinstance(node, TreeflowNode)
    assert "Cumprimentar" in node.objetivo
    assert node.collects[0].field == "segmento"
    assert node.collects[0].required is True
    assert node.next_nodes[0].target == "qualificacao"
    assert node.next_nodes[0].condition == "true"


def test_unknown_entry_node_raises() -> None:
    yaml_bad = """
schema_version: 1
id: bad
version: "1"
sdr_persona: {voice: "x", conduct: "x", examples: []}
entry_node: ghost_node
nodes:
  - id: only_node
    objetivo: x
    bridge_instruction: ""
    collects: []
    exit_condition: {type: all_fields_filled}
    next_nodes: []
"""
    with pytest.raises(TreeflowLoadError) as exc:
        load_treeflow_v2(yaml_bad)
    assert "entry_node" in str(exc.value)
    assert "ghost_node" in str(exc.value)


def test_unknown_transition_target_raises() -> None:
    yaml_bad = """
schema_version: 1
id: bad
version: "1"
sdr_persona: {voice: "x", conduct: "x", examples: []}
entry_node: start
nodes:
  - id: start
    objetivo: x
    bridge_instruction: ""
    collects: []
    exit_condition: {type: all_fields_filled}
    next_nodes:
      - condition: "true"
        target: missing_target
"""
    with pytest.raises(TreeflowLoadError) as exc:
        load_treeflow_v2(yaml_bad)
    assert "missing_target" in str(exc.value)
