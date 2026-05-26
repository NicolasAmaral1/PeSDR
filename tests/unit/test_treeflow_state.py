"""Unit tests for TalkFlowState additions (Plan 4a)."""

from __future__ import annotations

import operator
from typing import get_type_hints

from ai_sdr.treeflow.state import ObjectionRecord, TalkFlowState


def test_objection_record_shape():
    rec: ObjectionRecord = {
        "objection_id": "preco",
        "detected_at_node": "qualificacao",
        "turn_index": 3,
        "quote": "tá muito caro",
    }
    assert rec["objection_id"] == "preco"


def test_state_has_new_fields():
    """TalkFlowState declares the Plan-4a fields."""
    hints = get_type_hints(TalkFlowState, include_extras=True)
    assert "objections_handled" in hints
    assert "_origin_node_id" in hints
    assert "_active_objection" in hints
    assert "_classifier_result" in hints


def test_objections_handled_uses_operator_add_reducer():
    """objections_handled must be Annotated[..., operator.add] so LangGraph appends."""
    hints = get_type_hints(TalkFlowState, include_extras=True)
    annotated = hints["objections_handled"]
    # __metadata__ is the tuple of Annotated extras
    assert hasattr(annotated, "__metadata__")
    assert operator.add in annotated.__metadata__
