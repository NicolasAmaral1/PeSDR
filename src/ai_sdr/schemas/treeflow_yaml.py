"""Pydantic schemas for TreeFlow YAML files.

A TreeFlow is the static definition of a conversation funnel. It is compiled
into a LangGraph StateGraph at runtime (see ai_sdr.treeflow.compiler).

Plan 3 adds typed ``KBRef`` for ``NodeSpec.knowledge_base`` (spec §5.2).

Fields scoped out of plan 2 still accepted as forward-compatible opaque blobs:
- handles_objections is fully implemented in Plan 4a (objection classifier).
- sync_to_crm (Plan 5 — CRM)
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdr.schemas.llm_yaml import LLMConfig

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}[a-z0-9]$")
END_SENTINEL = "END"
BACK_TO_ORIGIN_SENTINEL = "BACK_TO_ORIGIN"

CollectType = Literal["text", "number", "boolean", "email", "phone"]
ExitConditionType = Literal["all_fields_filled", "rule_expression", "combined"]


class CollectField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    type: CollectType
    extraction_hint: str | None = None
    required: bool = False
    validation: dict[str, Any] | None = None


class ExitCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ExitConditionType
    expression: str | None = None
    fallback: Literal["llm_judge"] | None = None

    @model_validator(mode="after")
    def _expression_required_for_rule(self) -> ExitCondition:
        if self.type in {"rule_expression", "combined"} and not self.expression:
            raise ValueError(f"exit_condition.expression is required when type={self.type!r}")
        return self


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    target: str = Field(min_length=1)


class FollowUpStep(BaseModel):
    """One attempt in a TreeFlow's follow-up sequence.

    `after` is an ISO-8601 duration ("PT24H", "P3D", "P1W"). `template_ref` is the
    name of a Meta-registered HSM template; `params` are Jinja strings rendered at
    fire time against `collected`/`lead`/`tenant`.
    """

    model_config = ConfigDict(extra="forbid")

    after: str  # ISO-8601 duration
    template_ref: str = Field(min_length=1)
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)

    @field_validator("after")
    @classmethod
    def _check_iso_duration(cls, v: str) -> str:
        from ai_sdr.follow_up.duration import parse_duration

        try:
            parse_duration(v)
        except Exception as e:
            raise ValueError(f"invalid ISO-8601 duration {v!r}: {e}") from e
        return v


class FollowUpConfig(BaseModel):
    """TreeFlow-level follow-up declaration.

    `enabled` + `sequence` (with `template_ref`s pointing to Meta-registered HSM
    templates) + `max_attempts`. See spec §5 + §6.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)
    sequence: list[FollowUpStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_sequence_length(self) -> FollowUpConfig:
        if self.enabled and len(self.sequence) < self.max_attempts:
            raise ValueError(
                f"follow_up.sequence has {len(self.sequence)} entries but "
                f"max_attempts={self.max_attempts} — need at least max_attempts entries"
            )
        return self


class GlobalObjection(BaseModel):
    """TreeFlow-level objection (matches by id against classifier output)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = Field(default=None, min_length=1)  # node_id in same TreeFlow


class NodeObjection(BaseModel):
    """Per-Node objection ref. Replaces the dict[str, Any] forward-compat blob."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = Field(default=None, min_length=1)  # node_id in same TreeFlow


class KBRef(BaseModel):
    """Reference to a KB used by a Node (Plan 3, spec §5.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str = Field(min_length=1)
    llm: LLMConfig | None = None
    collects: list[CollectField] = Field(default_factory=list)
    exit_condition: ExitCondition
    next_nodes: list[Transition] = Field(min_length=1)

    knowledge_base: list[KBRef] | None = None

    handles_objections: list[NodeObjection] = Field(default_factory=list)
    sync_to_crm: str | None = None
    critical: bool = False

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, v: str) -> str:
        if not NODE_ID_RE.match(v):
            raise ValueError(
                "node id must be a slug: lowercase, digits, underscores; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        # Plan 4a — prevent collisions with the compiler's synthetic suffixes
        # (see CLASSIFIER_SUFFIX/INLINE_SUFFIX in ai_sdr.treeflow.compiler).
        if v.endswith("__classifier") or v.endswith("__inline"):
            raise ValueError(
                f"node id {v!r} ends with a reserved synthetic suffix "
                "('__classifier' or '__inline'); rename the node"
            )
        return v

    @model_validator(mode="after")
    def _validate_objection_ids_unique(self) -> NodeSpec:
        ids = [o.id for o in self.handles_objections]
        dupes = {x for x, n in Counter(ids).items() if n > 1}
        if dupes:
            raise ValueError(
                f"node {self.id!r} has duplicate handles_objections ids: {sorted(dupes)}"
            )
        return self


class TreeFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    display_name: str = Field(min_length=1)
    follow_up: FollowUpConfig | None = None
    global_objections: list[GlobalObjection] = Field(default_factory=list)
    entry_node: str
    nodes: list[NodeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_graph_consistency(self) -> TreeFlow:
        ids = [n.id for n in self.nodes]
        dupes = {x for x, n in Counter(ids).items() if n > 1}
        if dupes:
            raise ValueError(f"duplicate node ids: {sorted(dupes)}")

        valid_targets = set(ids) | {END_SENTINEL, BACK_TO_ORIGIN_SENTINEL}
        if self.entry_node not in ids:
            raise ValueError(
                f"entry_node={self.entry_node!r} is not declared in nodes (declared: {ids})"
            )
        for node in self.nodes:
            for tr in node.next_nodes:
                if tr.target not in valid_targets:
                    raise ValueError(
                        f"node {node.id!r} transitions to unknown target {tr.target!r} "
                        f"(must be one of: {sorted(valid_targets)})"
                    )

        # uniqueness of global_objections ids
        global_ids = [o.id for o in self.global_objections]
        global_dupes = {x for x, n in Counter(global_ids).items() if n > 1}
        if global_dupes:
            raise ValueError(f"duplicate global_objections ids: {sorted(global_dupes)}")

        # as_subnode references must point at existing nodes; also reject self-references
        all_objections: list[tuple[str, str | None, str | None]] = [
            (o.id, o.as_subnode, None) for o in self.global_objections
        ]
        for node in self.nodes:
            for o in node.handles_objections:
                all_objections.append((o.id, o.as_subnode, node.id))
        node_id_set = set(ids)
        for obj_id, subnode, declaring_node in all_objections:
            if subnode is None:
                continue
            if subnode not in node_id_set:
                raise ValueError(
                    f"objection {obj_id!r} as_subnode={subnode!r} is not declared "
                    f"in nodes (declared: {sorted(node_id_set)})"
                )
            if declaring_node is not None and subnode == declaring_node:
                raise ValueError(
                    f"objection {obj_id!r} in node {declaring_node!r} has as_subnode "
                    f"pointing to itself (would loop)"
                )

        # Warn when a node uses BACK_TO_ORIGIN but no objection references it via as_subnode.
        # Plan 4a: warning-only (the node may be referenced in a future version).
        subnode_targets = {sn for _, sn, _ in all_objections if sn is not None}
        for node in self.nodes:
            uses_back_to_origin = any(
                tr.target == BACK_TO_ORIGIN_SENTINEL for tr in node.next_nodes
            )
            if uses_back_to_origin and node.id not in subnode_targets:
                warnings.warn(
                    f"node {node.id!r} uses BACK_TO_ORIGIN in transitions but is not "
                    f"referenced by any objection's as_subnode — _origin_node_id will "
                    f"never be set, fallback to entry_node at runtime",
                    UserWarning,
                    stacklevel=2,
                )

        return self
