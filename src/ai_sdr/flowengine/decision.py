"""Pydantic schemas for the main LLM's structured output.

TurnDecision is the single shape the main LLM returns each turn. Every
side-effect the FlowEngine takes after the call is driven from fields
here: what to say (response_text), which fields to record
(collected_fields, extracted_facts), what state changes to enact
(next_node_suggestion, suggest_close_talk, request_human_escalation),
and self-attestations the LLM owes the runtime (treatment_status,
suspect_injection_attempt, reasoning).

The schema is bound via `with_structured_output(TurnDecision)` on the
main LLM in FE-01b.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TreatmentStrategy = Literal["inline", "subnode", "tool"]
TreatmentStatus = Literal["in_progress", "resolved_accepted", "resolved_deferred"]
CloseTalkSignal = Literal["no", "completed_success", "completed_failure", "no_interest"]
ResponseFormat = Literal["text", "voice", "both"]
EscalationCategory = Literal[
    "unknown_info",
    "out_of_scope",
    "complex_objection",
    "lead_requested",
    "sensitive_topic",
    "ambiguous_intent",
    "system_exhausted",
    "other",
]
Urgency = Literal["low", "medium", "high"]


class HumanEscalation(BaseModel):
    """Set on TurnDecision.request_human_escalation when the LLM asks for help."""

    reason: str = Field(min_length=10, max_length=300)
    category: EscalationCategory
    urgency: Urgency
    suggested_response: str | None = None
    waiting_message: str | None = None


class TurnDecision(BaseModel):
    """The single structured output of the main LLM per turn."""

    model_config = ConfigDict(extra="forbid")

    # The response to send to the lead
    response_text: str = Field(min_length=1)
    response_format: ResponseFormat | None = None
    voice_emotion: str | None = None

    # Fields extracted from this turn (per current node's `collects` schema)
    collected_fields: dict[str, Any]

    # Optional facts about the lead (short-term memory)
    extracted_facts: dict[str, Any] = Field(default_factory=dict)

    # Objection detection
    detected_objection: str | None = None
    treatment_strategy: TreatmentStrategy | None = None

    # Treatment resolution (when active_treatment was in progress).
    # Only meaningful when state.active_treatment is set; ignored otherwise.
    treatment_status: TreatmentStatus | None = None

    # Routing
    next_node_suggestion: str | None = None
    intends_to_advance: bool = False

    # Talk closure signal
    suggest_close_talk: CloseTalkSignal = "no"

    # Human escalation
    request_human_escalation: HumanEscalation | None = None

    # Prompt injection self-flag
    suspect_injection_attempt: bool = False

    # Off-topic detection (FE-03a brecha A1)
    off_topic_detected: bool = False

    # Reasoning (audit + debugging)
    reasoning: str = Field(min_length=1, max_length=400)


class JudgeVerdict(BaseModel):
    """Dedicated exit_condition judge LLM response (see spec §11.2)."""

    should_exit: bool
    reasoning: str = Field(min_length=1, max_length=200)
