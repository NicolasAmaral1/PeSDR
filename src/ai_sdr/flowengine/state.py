"""Pydantic shapes for TalkFlowState JSONB payloads.

These types validate the structured fields that the FlowEngine stores in
``talkflow_states`` (messages list, active_treatment, objections_handled,
talkflow_stack). The DB column is JSONB; Pydantic enforces structure at
the application boundary.

Stable for FE-01a; runtime that USES these lives in FE-01b and later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MessageRole = Literal["user", "assistant"]
MessageSource = Literal["lead", "agent", "operator"]
MediaType = Literal["text", "audio", "image", "video"]
ObjectionResolution = Literal["accepted", "deferred", "exhausted"]


class Message(BaseModel):
    """One entry in TalkFlowState.messages rolling window."""

    role: MessageRole
    content: str
    source: MessageSource
    media_type: MediaType = "text"
    media_storage_key: str | None = None
    turn_index: int = Field(ge=1)
    timestamp: datetime


class ActiveTreatment(BaseModel):
    """Active objection treatment state, set when a treatment is in progress."""

    objection_id: str
    started_at_turn: int = Field(ge=1)
    current_treatment_turn: int = Field(ge=1)
    max_treatment_turns: int = Field(ge=1)
    resolution_criteria: str = Field(min_length=1)
    treatment_history: list[str] = Field(default_factory=list)


class ObjectionHistoryEntry(BaseModel):
    """Record of an objection that was previously detected (resolved or not)."""

    objection_id: str
    detected_at_turn: int = Field(ge=1)
    resolved_at_turn: int | None = None
    resolution: ObjectionResolution | None = None


class StackFrame(BaseModel):
    """Sub-talk frame (V2 subflow capability). V1 always [single_frame]."""

    node_id: str
    entered_at_turn: int = Field(ge=1)
    return_to_node_id: str | None = None
