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
        if detected:
            obj = _find_objection(detected, treeflow, current_node_id)
            if obj is None:
                return StateDelta(
                    new_active_treatment=None,
                    events=[("objection.hallucinated_id", {"id_received": detected})],
                )
            if obj.treatment_mode == "tool":
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
            # inline mode: emit nothing (LLM handled within response_text)
        return StateDelta(new_active_treatment=None)

    # ACTIVE branch — priority order per spec §4.3:
    # 1. cross-objection (T21)
    # 2. max turns (T20)
    # 3. resolved_accepted
    # 4. resolved_deferred
    # 5. default (continue)

    # Priority 1: cross-objection (new id, must also be tool mode)
    detected = decision.detected_objection
    if (
        detected is not None
        and detected != active["objection_id"]
        and _is_tool_mode(detected, treeflow, current_node_id)
    ):
        new = _enter_treatment(detected, treeflow, current_node_id)
        return StateDelta(
            new_active_treatment=new,
            appended_objection_history=[
                {
                    "objection_id": active["objection_id"],
                    "detected_at_turn": active["started_at_turn"],
                    "resolved_at_turn": active["current_treatment_turn"],
                    "resolution": "deferred",
                }
            ],
            events=[
                (
                    "objection.treatment.cross_swap",
                    {"from_id": active["objection_id"], "to_id": detected},
                )
            ],
        )

    # Priority 2: max turns exhausted
    if active["current_treatment_turn"] >= active["max_treatment_turns"]:
        obj = _find_objection(
            active["objection_id"],
            treeflow,
            current_node_id,
        )
        action = (
            obj.tool_payload.on_max_turns_no_resolution.action
            if obj and obj.tool_payload
            else "gracefully_continue"
        )
        review_reason = "objection_treatment_exhausted" if action == "escalate_to_human" else None
        return StateDelta(
            new_active_treatment=None,
            appended_objection_history=[
                {
                    "objection_id": active["objection_id"],
                    "detected_at_turn": active["started_at_turn"],
                    "resolved_at_turn": active["current_treatment_turn"],
                    "resolution": "exhausted",
                }
            ],
            requires_review_reason=review_reason,
            events=[
                (
                    "objection.treatment.exhausted",
                    {
                        "objection_id": active["objection_id"],
                        "action_taken": action,
                    },
                )
            ],
        )

    # Priority 3 + 4: resolved
    if decision.treatment_status in ("resolved_accepted", "resolved_deferred"):
        resolution = "accepted" if decision.treatment_status == "resolved_accepted" else "deferred"
        return StateDelta(
            new_active_treatment=None,
            appended_objection_history=[
                {
                    "objection_id": active["objection_id"],
                    "detected_at_turn": active["started_at_turn"],
                    "resolved_at_turn": active["current_treatment_turn"],
                    "resolution": resolution,
                }
            ],
            events=[
                (
                    "objection.treatment.resolved",
                    {
                        "objection_id": active["objection_id"],
                        "status": resolution,
                        "total_turns": active["current_treatment_turn"],
                    },
                )
            ],
        )

    # Priority 5: default continue (existing from T18, unchanged)
    new = dict(active)
    new["current_treatment_turn"] = active["current_treatment_turn"] + 1
    return StateDelta(
        new_active_treatment=new,
        events=[
            (
                "objection.treatment.continued",
                {
                    "objection_id": active["objection_id"],
                    "current_turn": new["current_treatment_turn"],
                },
            )
        ],
    )
