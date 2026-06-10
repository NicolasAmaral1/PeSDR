"""Objection treatment state machine — pure function (FE-03a §4).

apply(state, decision, treeflow) -> StateDelta

The function reads the runtime state (a TalkFlowState-like dict or the
ORM model — both expose .active_treatment via attribute or key), the
LLM's TurnDecision, and the TreeFlow definition. It returns a delta
describing what should be persisted.

This module DOES NOT touch the DB. post_processing.apply_decision is
responsible for translating the delta into ORM mutations.

States: IDLE (active_treatment is None) and ACTIVE (active_treatment set).
See spec §4.2 for transition diagram and §4.3 for priority order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowObjection,
)

logger = logging.getLogger(__name__)


_UNSET = object()


@dataclass
class StateDelta:
    """Delta describing the state changes to apply.

    `unchanged` means: no field overrides at all.
    """

    new_active_treatment: dict[str, Any] | None | object = field(default_factory=lambda: _UNSET)
    appended_objection_history: list[dict[str, Any]] = field(default_factory=list)
    requires_review_reason: str | None = None
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def changes_treatment(self) -> bool:
        return self.new_active_treatment is not _UNSET


def _find_objection(
    objection_id: str,
    treeflow: TreeflowDef,
    current_node_id: str | None = None,
) -> TreeflowObjection | None:
    # Node-scoped takes precedence.
    if current_node_id is not None:
        node = treeflow.nodes.get(current_node_id)
        if node is not None:
            for obj in node.handles_objections:
                if obj.id == objection_id:
                    return obj
    for obj in treeflow.global_objections:
        if obj.id == objection_id:
            return obj
    return None


def _is_tool_mode(objection_id: str, treeflow: TreeflowDef, current_node_id: str | None) -> bool:
    obj = _find_objection(objection_id, treeflow, current_node_id)
    return obj is not None and obj.treatment_mode == "tool"


def _enter_treatment(
    objection_id: str,
    treeflow: TreeflowDef,
    current_node_id: str | None,
) -> dict[str, Any]:
    obj = _find_objection(objection_id, treeflow, current_node_id)
    # obj is guaranteed tool here (caller checked).
    assert obj is not None and obj.tool_payload is not None
    tp = obj.tool_payload
    return {
        "objection_id": obj.id,
        "started_at_turn": 1,
        "current_treatment_turn": 1,
        "max_treatment_turns": tp.max_treatment_turns,
        "resolution_criteria": tp.resolution_criteria,
        "treatment_history": [],
    }


def _state_attr(state: Any, key: str, default: Any = None) -> Any:
    """Read either a dict or a SQLAlchemy ORM object."""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def apply(
    *,
    state: Any,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> StateDelta:
    """Run the state machine. Pure function. See module docstring."""
    active = _state_attr(state, "active_treatment")
    current_node_id = _state_attr(state, "current_node")

    # IDLE
    if active is None:
        detected = decision.detected_objection
        if detected and _is_tool_mode(detected, treeflow, current_node_id):
            new = _enter_treatment(detected, treeflow, current_node_id)
            return StateDelta(
                new_active_treatment=new,
                events=[
                    (
                        "objection.treatment.entered",
                        {
                            "objection_id": detected,
                            "max_turns": new["max_treatment_turns"],
                        },
                    )
                ],
            )
        # Stay IDLE: explicitly assert no active treatment.
        return StateDelta(new_active_treatment=None)

    # ACTIVE — not yet implemented in this task; will be filled in T18-T22.
    return StateDelta()
