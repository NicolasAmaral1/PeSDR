"""Pydantic schemas validating tenant YAML configuration.

Only the subset required for the foundation plan is implemented here.
Later plans extend with crm, messaging, llm, media, guardrails, treeflows.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdr.schemas.llm_yaml import LLMDefaults

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


class GuardrailsConfig(BaseModel):
    """Tenant-level guardrails configuration (Plan 3, spec §4.5)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    allowed_prices: list[int] = Field(default_factory=list)
    allowed_products: list[str] = Field(default_factory=list)
    critic_enabled: bool = True
    fallback_text: str = Field(min_length=10)
    max_retries: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def _require_lists_when_enabled(self) -> Self:
        if self.enabled and not self.allowed_prices and not self.allowed_products:
            raise ValueError(
                "guardrails.enabled=true requires at least one of "
                "allowed_prices or allowed_products to be non-empty"
            )
        return self


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    timezone: str
    schedule: ScheduleConfig | None = None
    conversation: ConversationConfig | None = None
    llm: LLMDefaults | None = None
    guardrails: GuardrailsConfig | None = None

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "id must be a slug: lowercase, digits, hyphens; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v
