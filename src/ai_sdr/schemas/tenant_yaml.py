"""Pydantic schemas validating tenant YAML configuration.

Only the subset required for the foundation plan is implemented here.
Later plans extend with crm, messaging, llm, media, guardrails, treeflows.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Self

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
    disallowed_price_pattern: str = ""

    @model_validator(mode="after")
    def _require_lists_when_enabled(self) -> Self:
        if self.enabled and not self.allowed_prices and not self.allowed_products:
            raise ValueError(
                "guardrails.enabled=true requires at least one of "
                "allowed_prices or allowed_products to be non-empty"
            )
        return self


class ReengagementTemplate(BaseModel):
    """Tenant-level default template used by WindowExpiredError recovery.

    When the worker's send_text raises WindowExpiredError (lead silent >24h),
    the worker falls back to send_template with this config. If the tenant
    omits this block, recovery falls back to plain error logging.
    """

    model_config = ConfigDict(extra="forbid")

    template_ref: str = Field(min_length=1)
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)


class MessagingConfig(BaseModel):
    """Messaging provider config. provider is free-form; factory dispatches.

    For provider='whatsapp_cloud', the four *_ref fields are required and
    must use the 'secrets/' prefix (resolved by SopsLoader at runtime).
    """

    model_config = ConfigDict(extra="forbid")

    provider: str
    phone_number_id_ref: str | None = None
    access_token_ref: str | None = None
    webhook_verify_token_ref: str | None = None
    app_secret_ref: str | None = None
    api_version: str = "v21.0"
    reengagement_template: ReengagementTemplate | None = None

    @model_validator(mode="after")
    def _check_provider_fields(self) -> Self:
        if self.provider == "whatsapp_cloud":
            required = (
                "phone_number_id_ref",
                "access_token_ref",
                "webhook_verify_token_ref",
                "app_secret_ref",
            )
            for f in required:
                v = getattr(self, f)
                if not v:
                    raise ValueError(f"messaging.whatsapp_cloud requires {f}")
                if not v.startswith("secrets/"):
                    raise ValueError(f"messaging.{f} must start with 'secrets/' (got {v!r})")
        return self


class ObjectionsConfig(BaseModel):
    """Tenant-level objection classifier configuration (Plan 4a, spec §4.2)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    max_handled_per_lead: int = Field(default=10, ge=1, le=100)
    history_window: int = Field(default=4, ge=1, le=20)


class ConsoleConfig(BaseModel):
    """Operator console toggle per tenant (Plano 11).

    enabled=true exposes /console/{slug}/... for this tenant. Credentials
    do NOT live here — see the users table + user_tenant_access in
    migration 0009 + spec §5. Tenants that use Vialum Tasks Inbox as
    their HITL surface should set enabled=false (or omit the block).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    timezone: str
    schedule: ScheduleConfig | None = None
    conversation: ConversationConfig | None = None
    console: ConsoleConfig | None = None  # Plan 11
    llm: LLMDefaults | None = None
    messaging: MessagingConfig | None = None
    guardrails: GuardrailsConfig | None = None
    objections: ObjectionsConfig | None = None  # Plan 4a
    sdr_persona: dict[str, Any] | None = None  # FE-01b: pass-through slot (architecture_version stays in DB)

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "id must be a slug: lowercase, digits, hyphens; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v
