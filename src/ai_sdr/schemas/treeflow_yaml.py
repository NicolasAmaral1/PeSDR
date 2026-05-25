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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdr.schemas.llm_yaml import LLMConfig

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}[a-z0-9]$")
END_SENTINEL = "END"

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
    model_config = ConfigDict(extra="forbid")

    after: str = Field(pattern=r"^\d+(s|m|h|d)$")  # e.g. "24h", "30m", "7d"
    template: str = Field(min_length=1)


class FollowUpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    sequence: list[FollowUpStep] = Field(default_factory=list)


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
        return v


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
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"duplicate node ids: {sorted(dupes)}")

        valid_targets = set(ids) | {END_SENTINEL}
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
        return self
