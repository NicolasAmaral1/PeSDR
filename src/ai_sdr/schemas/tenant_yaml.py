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
    fallback_text: str = ""
    max_retries: int = Field(default=2, ge=1, le=5)
    disallowed_price_pattern: str = ""

    @model_validator(mode="after")
    def _check_required_when_enabled(self) -> Self:
        """FE-03a Task 10: spec §7.2 — allowed_products + fallback_text are
        mandatory when guardrails are enabled. When disabled, the runner is
        a passthrough so the lists may be empty.
        """
        if not self.enabled:
            return self
        if not self.allowed_products:
            raise ValueError("guardrails.allowed_products must be non-empty when enabled")
        if not self.fallback_text or len(self.fallback_text) < 10:
            raise ValueError(
                "guardrails.fallback_text must be a non-empty string of >=10 chars when enabled"
            )
        return self


class HumanizationConfig(BaseModel):
    """Per-tenant humanization knobs (FE-03b §4).

    All defaults align with the runtime humanizer; tenants without the
    `humanization` block in their tenant.yaml inherit these.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = Field(default=8.0, gt=0)
    chars_per_second_max: float = Field(default=15.0, gt=0)
    min_delay_ms: int = Field(default=800, ge=0)
    max_delay_ms: int = Field(default=4000, ge=0)
    apply_to_voice: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.chars_per_second_min > self.chars_per_second_max:
            raise ValueError("humanization.chars_per_second_min must be <= chars_per_second_max")
        if self.min_delay_ms > self.max_delay_ms:
            raise ValueError("humanization.min_delay_ms must be <= max_delay_ms")
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


def _require_secrets_prefix(value: str | None) -> str | None:
    if value is not None and not value.startswith("secrets/"):
        raise ValueError(f"ref must start with 'secrets/' (got {value!r})")
    return value


class SpeechSynthesisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    credentials_ref: str
    voice_id: str
    format: str = "ogg_opus"
    timeout_seconds: int = Field(default=8, ge=1, le=60)
    default_emotion: str | None = None

    @field_validator("credentials_ref")
    @classmethod
    def _check_ref(cls, v: str) -> str:
        return _require_secrets_prefix(v)  # type: ignore[return-value]


class SpeechTranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    credentials_ref: str
    language: str = "pt-BR"
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("credentials_ref")
    @classmethod
    def _check_ref(cls, v: str) -> str:
        return _require_secrets_prefix(v)  # type: ignore[return-value]


class VoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_mode: Literal["always", "match_lead", "never", "context_driven"] = "never"
    fallback_to_text_on_failure: bool = True
    synthesis: SpeechSynthesisConfig | None = None
    transcription: SpeechTranscriptionConfig | None = None

    @model_validator(mode="after")
    def _check_synthesis_present(self) -> Self:
        if self.response_mode != "never" and self.synthesis is None:
            raise ValueError("voice.synthesis is required when response_mode != 'never'")
        return self


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    bucket: str
    endpoint_ref: str | None = None
    access_key_ref: str | None = None
    secret_key_ref: str | None = None

    @field_validator("endpoint_ref", "access_key_ref", "secret_key_ref")
    @classmethod
    def _check_refs(cls, v: str | None) -> str | None:
        return _require_secrets_prefix(v)


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
    humanization: HumanizationConfig = Field(default_factory=HumanizationConfig)
    voice: VoiceConfig | None = None
    storage: StorageConfig | None = None
    sdr_persona: dict[str, Any] | None = (
        None  # FE-01b: pass-through slot (architecture_version stays in DB)
    )

    @field_validator("id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "id must be a slug: lowercase, digits, hyphens; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v
