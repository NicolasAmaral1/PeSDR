"""TalkFlow state — the typed dict LangGraph persists per thread.

LangGraph treats the state as immutable per node: each node returns a partial dict;
LangGraph merges via per-field reducers. Fields without explicit reducers use
"replace" (the new value wins). For lists we want "append," so we annotate.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Message(TypedDict):
    role: str  # "user" | "assistant" | "system"
    content: str


class TalkFlowState(TypedDict, total=False):
    # identity (set on create, never mutated)
    tenant_id: str
    lead_id: str
    treeflow_id: str
    treeflow_version: str

    # turn-by-turn dynamic fields
    current_node: str
    collected: dict[str, Any]  # accumulated across nodes; merged via dict.update
    messages: Annotated[list[Message], operator.add]
    last_user_input: str
    last_agent_response: str
    completed: bool  # True when graph reached END
