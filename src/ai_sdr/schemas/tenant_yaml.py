"""Pydantic schemas validating tenant YAML configuration.

Only the subset required for the foundation plan is implemented here.
Later plans extend with crm, messaging, llm, media, guardrails, treeflows.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mon_fri: str | None = Field(default=None, alias="mon-fri")
    sat: str | None = None
    sun: str | None = None
    off_hours_behavior: Literal["queue", "respond_with_notice"] = "queue"


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    debounce_ms: int = Field(default=5000, ge=0, le=60_000)
    optout_stop_words: list[str] = Field(default_factory=list)
    optout_action: Literal["end_conversation_silent", "send_confirmation"] = (
        "end_conversation_silent"
    )


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    timezone: str
    schedule: ScheduleConfig | None = None
    conversation: ConversationConfig | None = None

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "id must be a slug: lowercase, digits, hyphens; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v
