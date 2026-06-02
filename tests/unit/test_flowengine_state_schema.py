"""Pydantic shapes for TalkFlowState JSONB payloads."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.state import (
    ActiveTreatment,
    Message,
    ObjectionHistoryEntry,
    StackFrame,
)


def test_message_round_trip() -> None:
    m = Message(
        role="user",
        content="oi",
        source="lead",
        turn_index=1,
        timestamp=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )
    dumped = m.model_dump(mode="json")
    assert dumped["role"] == "user"
    assert dumped["source"] == "lead"
    assert dumped["media_type"] == "text"
    reloaded = Message.model_validate(dumped)
    assert reloaded == m


def test_message_audio_with_storage_key() -> None:
    m = Message(
        role="user",
        content="(audio: ...)",
        source="lead",
        turn_index=1,
        timestamp=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
        media_type="audio",
        media_storage_key="s3://bucket/key.ogg",
    )
    assert m.media_storage_key == "s3://bucket/key.ogg"


def test_message_role_validates() -> None:
    with pytest.raises(ValidationError):
        Message(
            role="unknown",  # type: ignore[arg-type]
            content="x",
            source="lead",
            turn_index=1,
            timestamp=datetime.now(timezone.utc),
        )


def test_active_treatment_round_trip() -> None:
    at = ActiveTreatment(
        objection_id="preco",
        started_at_turn=3,
        current_treatment_turn=2,
        max_treatment_turns=3,
        resolution_criteria="lead aceitou parcelamento",
        treatment_history=["argued ROI"],
    )
    reloaded = ActiveTreatment.model_validate(at.model_dump(mode="json"))
    assert reloaded.objection_id == "preco"
    assert reloaded.current_treatment_turn == 2


def test_objection_history_entry_validates_resolution() -> None:
    e = ObjectionHistoryEntry(
        objection_id="preco",
        detected_at_turn=2,
        resolved_at_turn=5,
        resolution="accepted",
    )
    assert e.resolution == "accepted"


def test_stack_frame_default_marker() -> None:
    """V1 always has a single placeholder frame for forward compat."""
    f = StackFrame(node_id="saudacao", entered_at_turn=1)
    assert f.return_to_node_id is None
