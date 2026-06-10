"""TreeFlowLoader parses node.handles_objections (FE-03a Task 6)."""

from __future__ import annotations

from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "avelum_v2_node_objections.yaml"


def test_handles_objections_loaded_on_node():
    tf = load_treeflow_v2(FIXTURE.read_text())
    node = tf.nodes["qualificacao"]
    assert hasattr(node, "handles_objections")
    assert len(node.handles_objections) == 1


def test_handles_objection_has_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    obj = tf.nodes["qualificacao"].handles_objections[0]
    assert obj.id == "ja_tentei_curso_online"
    assert obj.treatment_mode == "tool"
    assert obj.tool_payload.max_treatment_turns == 2


def test_node_without_handles_objections_defaults_empty():
    """A node that omits the block must yield an empty list, not raise."""
    other = Path(__file__).resolve().parent.parent / "fixtures" / "avelum_v2_with_objections.yaml"
    tf = load_treeflow_v2(other.read_text())
    assert tf.nodes["saudacao"].handles_objections == []
