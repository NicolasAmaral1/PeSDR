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


def test_off_topic_count_legacy_payload_without_field_loads_clean():
    """Existing serialized state without off_topic_count must default to 0."""
    legacy = {"messages": [], "objections_handled": []}
    p = TalkFlowStatePayload.model_validate(legacy)
    assert p.off_topic_count == 0
