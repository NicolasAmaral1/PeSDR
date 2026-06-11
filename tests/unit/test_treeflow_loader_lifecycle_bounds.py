"""TreeFlowLoader bounds validation for talk_lifecycle (FE-03b Task 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2

F = Path(__file__).resolve().parent.parent / "fixtures"


def test_rejects_invalid_iso_duration():
    with pytest.raises(TreeflowLoadError, match="close_after_inactivity"):
        load_treeflow_v2((F / "treeflow_invalid_iso_duration.yaml").read_text())


def test_rejects_invalid_completion_expression_syntax():
    with pytest.raises(TreeflowLoadError, match="close_when_completed"):
        load_treeflow_v2((F / "treeflow_invalid_completion_expression.yaml").read_text())


def test_rejects_invalid_outcome():
    with pytest.raises(TreeflowLoadError, match="outcome"):
        load_treeflow_v2((F / "treeflow_invalid_outcome.yaml").read_text())


def test_rejects_inactivity_below_min_bound():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_inactivity: PT30M  # 30 minutes; below minimum PT1H
"""
    with pytest.raises(TreeflowLoadError, match="PT1H"):
        load_treeflow_v2(yaml_text)


def test_rejects_inactivity_above_max_bound():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_inactivity: P400D  # 400 days; above maximum P365D
"""
    with pytest.raises(TreeflowLoadError, match="P365D"):
        load_treeflow_v2(yaml_text)


def test_rejects_duration_out_of_bounds():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_duration: PT12H  # 12 hours; below minimum P1D
"""
    with pytest.raises(TreeflowLoadError, match="P1D"):
        load_treeflow_v2(yaml_text)
