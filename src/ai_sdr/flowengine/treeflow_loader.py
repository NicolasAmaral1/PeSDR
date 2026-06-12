"""Minimal TreeFlow v2 YAML loader.

Parses the subset of the v2 schema FE-01b consumes: sdr_persona,
entry_node, and per-node objetivo/collects/bridge_instruction/exit/
next_nodes. Future plans (FE-03+) extend this loader with objection
treatment, lifecycle rules, action triggers, etc.

Returns @dataclass types (not Pydantic) — config plumbing, not LLM I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml


class TreeflowLoadError(ValueError):
    """Raised when the YAML is structurally invalid for FE-01b's subset."""


@dataclass
class TreeflowCollectField:
    field: str
    type: str
    required: bool = False
    extraction_hint: str | None = None


@dataclass
class TreeflowExitCondition:
    type: str  # "all_fields_filled" | "rule_expression" | "combined" | "llm_judge"
    expression: str | None = None  # for rule_expression / combined
    fallback: str | None = None  # for combined -> "llm_judge"


@dataclass
class TreeflowTransition:
    condition: str  # "true" or a simpleeval expression
    target: str


@dataclass
class TreeflowOnMaxTurns:
    action: str  # "gracefully_continue" | "escalate_to_human"
    message_hint: str | None = None


@dataclass
class TreeflowToolPayload:
    canonical_arguments_summary: str
    kb_ref: str
    max_treatment_turns: int
    resolution_criteria: str
    on_max_turns_no_resolution: TreeflowOnMaxTurns
    expected_turns: int | None = None


@dataclass
class TreeflowObjection:
    id: str
    description: str
    treatment_mode: str  # "tool" | "inline"
    tool_payload: TreeflowToolPayload | None = None


@dataclass
class TreeflowNode:
    id: str
    objetivo: str
    bridge_instruction: str
    collects: list[TreeflowCollectField]
    exit_condition: TreeflowExitCondition
    next_nodes: list[TreeflowTransition]
    handles_objections: list[TreeflowObjection] = field(default_factory=list)


@dataclass
class TreeflowDef:
    id: str
    version: str
    display_name: str | None
    sdr_persona: dict[str, Any]  # voice + conduct + examples — raw dict
    entry_node: str
    nodes: dict[str, TreeflowNode]
    global_objections: list[TreeflowObjection] = field(default_factory=list)


def load_treeflow_v2(yaml_text: str) -> TreeflowDef:
    """Parse YAML into a TreeflowDef. Raises TreeflowLoadError on issues."""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise TreeflowLoadError(f"invalid YAML: {e}") from e

    if not isinstance(data, dict):
        raise TreeflowLoadError("root of TreeFlow YAML must be a mapping")

    required = {"id", "version", "sdr_persona", "entry_node", "nodes"}
    missing = required - data.keys()
    if missing:
        raise TreeflowLoadError(f"missing required fields: {sorted(missing)}")

    nodes_raw = data["nodes"]
    if not isinstance(nodes_raw, list):
        raise TreeflowLoadError("'nodes' must be a list")

    nodes: dict[str, TreeflowNode] = {}
    for raw in nodes_raw:
        nodes[raw["id"]] = _parse_node(raw)

    entry = data["entry_node"]
    if entry not in nodes:
        raise TreeflowLoadError(f"entry_node {entry!r} does not match any defined node id")

    for node in nodes.values():
        for tr in node.next_nodes:
            if tr.target not in nodes:
                raise TreeflowLoadError(
                    f"transition target {tr.target!r} in node {node.id!r} "
                    f"does not match any defined node id"
                )

    global_objections = [_parse_objection(o) for o in data.get("global_objections", [])]

    return TreeflowDef(
        id=data["id"],
        version=str(data["version"]),
        display_name=data.get("display_name"),
        sdr_persona=data["sdr_persona"],
        entry_node=entry,
        nodes=nodes,
        global_objections=global_objections,
    )


def _parse_node(raw: dict[str, Any]) -> TreeflowNode:
    required = {"id", "objetivo", "collects", "exit_condition", "next_nodes"}
    missing = required - raw.keys()
    if missing:
        raise TreeflowLoadError(f"node missing fields {sorted(missing)}: {raw!r}")

    collects = [
        TreeflowCollectField(
            field=c["field"],
            type=c["type"],
            required=c.get("required", False),
            extraction_hint=c.get("extraction_hint"),
        )
        for c in raw["collects"]
    ]

    ec = raw["exit_condition"]
    exit_cond = TreeflowExitCondition(
        type=ec["type"],
        expression=ec.get("expression"),
        fallback=ec.get("fallback"),
    )

    transitions = [
        TreeflowTransition(condition=str(t["condition"]), target=t["target"])
        for t in raw["next_nodes"]
    ]

    handles_objections = [_parse_objection(o) for o in raw.get("handles_objections", [])]

    return TreeflowNode(
        id=raw["id"],
        objetivo=raw["objetivo"],
        bridge_instruction=raw.get("bridge_instruction", ""),
        collects=collects,
        exit_condition=exit_cond,
        next_nodes=transitions,
        handles_objections=handles_objections,
    )


_ALLOWED_MODES = {"tool", "inline"}
_ALLOWED_MAX_TURNS_ACTIONS = {"gracefully_continue", "escalate_to_human"}


def _parse_objection(raw: dict[str, Any]) -> TreeflowObjection:
    required = {"id", "description", "treatment_mode"}
    missing = required - raw.keys()
    if missing:
        raise TreeflowLoadError(f"objection missing fields {sorted(missing)}: {raw!r}")
    desc = str(raw["description"])
    if len(desc) < 10:
        raise TreeflowLoadError(f"objection {raw['id']!r}: description must be >=10 chars")
    mode = raw["treatment_mode"]
    if mode not in _ALLOWED_MODES:
        raise TreeflowLoadError(
            f"objection {raw['id']!r}: treatment_mode must be one of "
            f"{sorted(_ALLOWED_MODES)}, got {mode!r}"
        )
    payload: TreeflowToolPayload | None = None
    if mode == "tool":
        tp = raw.get("tool_payload")
        if not isinstance(tp, dict):
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: treatment_mode=tool requires tool_payload mapping"
            )
        mtt = int(tp.get("max_treatment_turns", 0))
        if not 1 <= mtt <= 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: max_treatment_turns must be in [1, 10], got {mtt}"
            )
        cas = str(tp.get("canonical_arguments_summary", ""))
        if len(cas) < 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: canonical_arguments_summary must be >=10 chars"
            )
        rc = str(tp.get("resolution_criteria", ""))
        if len(rc) < 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: resolution_criteria must be >=10 chars"
            )
        omtr_raw = tp.get("on_max_turns_no_resolution") or {}
        action = omtr_raw.get("action")
        if action not in _ALLOWED_MAX_TURNS_ACTIONS:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: on_max_turns_no_resolution.action "
                f"must be one of {sorted(_ALLOWED_MAX_TURNS_ACTIONS)}, "
                f"got {action!r}"
            )
        payload = TreeflowToolPayload(
            canonical_arguments_summary=cas,
            kb_ref=tp.get("kb_ref", ""),
            max_treatment_turns=mtt,
            resolution_criteria=rc,
            expected_turns=tp.get("expected_turns"),
            on_max_turns_no_resolution=TreeflowOnMaxTurns(
                action=action,
                message_hint=omtr_raw.get("message_hint"),
            ),
        )
    return TreeflowObjection(
        id=str(raw["id"]),
        description=desc,
        treatment_mode=mode,
        tool_payload=payload,
    )
