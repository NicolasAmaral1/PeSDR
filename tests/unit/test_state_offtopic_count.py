"""TalkFlowState.off_topic_count default + validation (FE-03a Task 2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# off_topic_count lives in the model TalkFlowState (SQLAlchemy) but is
# proxied through a Pydantic payload schema. We test the Pydantic shape
# that gets serialised into the JSONB column.
from ai_sdr.flowengine.state import TalkFlowStatePayload  # NEW class


def test_off_topic_count_defaults_zero():
    p = TalkFlowStatePayload()
    assert p.off_topic_count == 0


def test_off_topic_count_accepts_positive_int():
    p = TalkFlowStatePayload(off_topic_count=5)
    assert p.off_topic_count == 5


def test_off_topic_count_rejects_negative():
    with pytest.raises(ValidationError):
        TalkFlowStatePayload(off_topic_count=-1)


def test_legacy_payload_missing_field_defaults_to_zero():
    """Existing serialized state without off_topic_count must default to 0."""
    legacy = {"messages": [], "objections_handled": []}
    p = TalkFlowStatePayload.model_validate(legacy)
    assert p.off_topic_count == 0


def test_unknown_keys_preserved_via_extra_allow():
    """extra='allow' means unknown JSONB keys round-trip without loss."""
    raw = {"off_topic_count": 2, "future_flag": "x", "another_flag": [1, 2]}
    p = TalkFlowStatePayload.model_validate(raw)
    dumped = p.model_dump()
    assert dumped["off_topic_count"] == 2
    assert dumped["future_flag"] == "x"
    assert dumped["another_flag"] == [1, 2]
