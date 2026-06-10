"""TreeFlowLoader parses global_objections + tool_payload (FE-03a Task 5)."""

from __future__ import annotations

from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "avelum_v2_with_objections.yaml"


def test_global_objections_loaded():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert hasattr(tf, "global_objections")
    assert len(tf.global_objections) == 2


def test_global_objections_indexed_by_id():
    tf = load_treeflow_v2(FIXTURE.read_text())
    by_id = {o.id: o for o in tf.global_objections}
    assert "preco" in by_id
    assert "pediu_downsell" in by_id


def test_tool_objection_carries_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    preco = next(o for o in tf.global_objections if o.id == "preco")
    assert preco.treatment_mode == "tool"
    assert preco.tool_payload is not None
    assert preco.tool_payload.max_treatment_turns == 3
    assert preco.tool_payload.kb_ref == "argumentos_preco"
    assert preco.tool_payload.on_max_turns_no_resolution.action == "gracefully_continue"


def test_inline_objection_has_no_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    ds = next(o for o in tf.global_objections if o.id == "pediu_downsell")
    assert ds.treatment_mode == "inline"
    assert ds.tool_payload is None
