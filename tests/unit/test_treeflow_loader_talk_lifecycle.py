"""TreeFlowLoader parses talk_lifecycle block (FE-03b Task 4)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "avelum_v2_with_lifecycle.yaml"
)


def test_talk_lifecycle_loaded():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert hasattr(tf, "talk_lifecycle")
    assert tf.talk_lifecycle is not None


def test_inactivity_parsed_as_timedelta():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert tf.talk_lifecycle.close_after_inactivity == timedelta(days=7)


def test_duration_parsed_as_timedelta():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert tf.talk_lifecycle.close_after_duration == timedelta(days=30)


def test_completion_rules_parsed():
    tf = load_treeflow_v2(FIXTURE.read_text())
    rules = tf.talk_lifecycle.close_when_completed
    assert len(rules) == 2
    assert rules[0].expression == "collected.demo_agendada == true"
    assert rules[0].outcome == "success"
    assert rules[1].outcome == "no_interest"


def test_treeflow_without_talk_lifecycle_block_returns_none():
    """Backward-compat: TreeFlows without the block load with None."""
    yaml_text = """
schema_version: 1
id: minimal
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
"""
    tf = load_treeflow_v2(yaml_text)
    assert tf.talk_lifecycle is None
