"""Pydantic schemas for LLM configuration (used by tenant.yaml and TreeFlow node overrides)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderName = Literal["anthropic", "openai"]


class LLMConfig(BaseModel):
    """A single LLM call configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=64_000)
    api_key_ref: str

    @field_validator("api_key_ref")
    @classmethod
    def _api_key_ref_is_a_secret_ref(cls, v: str) -> str:
        if not v.startswith("secrets/"):
            raise ValueError(
                "api_key_ref must reference a SOPS secret (e.g. 'secrets/anthropic_key'); "
                "never embed the key directly"
            )
        return v


class EmbeddingsConfig(BaseModel):
    """OpenAI embeddings config (used by KB indexer + retriever)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    model: str = "text-embedding-3-small"
    api_key_ref: str = "openai_key"


class LLMDefaults(BaseModel):
    """Tenant-level LLM defaults — Nodes inherit `default` unless they override."""

    model_config = ConfigDict(extra="forbid")

    default: LLMConfig
    classifier: LLMConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    cache_enabled: bool = True
