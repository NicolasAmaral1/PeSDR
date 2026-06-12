"""TreeFlowLoader bounds validation (FE-03a Task 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2

F = Path(__file__).resolve().parent.parent / "fixtures"


def test_rejects_max_turns_out_of_range():
    with pytest.raises(TreeflowLoadError, match="max_treatment_turns"):
        load_treeflow_v2((F / "treeflow_invalid_max_turns.yaml").read_text())


def test_rejects_unknown_treatment_mode():
    with pytest.raises(TreeflowLoadError, match="treatment_mode"):
        load_treeflow_v2((F / "treeflow_invalid_treatment_mode.yaml").read_text())


def test_rejects_missing_tool_payload_when_mode_tool():
    with pytest.raises(TreeflowLoadError, match="tool_payload"):
        load_treeflow_v2((F / "treeflow_missing_tool_payload.yaml").read_text())


def test_rejects_description_under_min_length():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "short"
    treatment_mode: inline
"""
    with pytest.raises(TreeflowLoadError, match="description"):
        load_treeflow_v2(yaml_text)


def test_rejects_unknown_on_max_turns_action():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "lead diz preço caro"
    treatment_mode: tool
    tool_payload:
      canonical_arguments_summary: "argumentos canonicos"
      kb_ref: kb
      max_treatment_turns: 3
      resolution_criteria: "criterio de resolucao"
      on_max_turns_no_resolution: { action: shoot_lead }
"""
    with pytest.raises(TreeflowLoadError, match="action"):
        load_treeflow_v2(yaml_text)
