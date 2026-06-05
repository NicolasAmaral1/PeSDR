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
class TreeflowNode:
    id: str
    objetivo: str
    bridge_instruction: str
    collects: list[TreeflowCollectField]
    exit_condition: TreeflowExitCondition
    next_nodes: list[TreeflowTransition]


@dataclass
class TreeflowDef:
    id: str
    version: str
    display_name: str | None
    sdr_persona: dict[str, Any]  # voice + conduct + examples — raw dict
    entry_node: str
    nodes: dict[str, TreeflowNode]


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
        raise TreeflowLoadError(
            f"entry_node {entry!r} does not match any defined node id"
        )

    for node in nodes.values():
        for tr in node.next_nodes:
            if tr.target not in nodes:
                raise TreeflowLoadError(
                    f"transition target {tr.target!r} in node {node.id!r} "
                    f"does not match any defined node id"
                )

    return TreeflowDef(
        id=data["id"],
        version=str(data["version"]),
        display_name=data.get("display_name"),
        sdr_persona=data["sdr_persona"],
        entry_node=entry,
        nodes=nodes,
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

    return TreeflowNode(
        id=raw["id"],
        objetivo=raw["objetivo"],
        bridge_instruction=raw.get("bridge_instruction", ""),
        collects=collects,
        exit_condition=exit_cond,
        next_nodes=transitions,
    )
