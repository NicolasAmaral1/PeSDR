"""Pydantic schemas for LLM configuration (used by tenant.yaml and TreeFlow node overrides)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_api_key_ref(v: str) -> str:
    """Enforce the SOPS-secret-ref invariant: api_key_ref must start with 'secrets/'.

    Prevents plaintext keys from being embedded directly in tenant.yaml.
    """
    if not v.startswith("secrets/"):
        raise ValueError(
            "api_key_ref must reference a SOPS secret (e.g. 'secrets/anthropic_key'); "
            "never embed the key directly"
        )
    return v


class LLMConfig(BaseModel):
    """A single LLM call configuration."""

    model_config = ConfigDict(extra="forbid")

    # Free-form string — dispatched via langchain.chat_models.init_chat_model.
    # Common values: "anthropic", "openai", "google_genai", "deepseek", "ollama",
    # "bedrock_converse", "vertexai", "mistralai". Whichever langchain-<x> package
    # is installed will work. Trade-off: we lose Literal's compile-time guarantee
    # of provider validity; validation that the runtime actually supports the
    # chosen provider happens lazily inside build_llm() / init_chat_model().
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=64_000)
    api_key_ref: str

    @field_validator("api_key_ref")
    @classmethod
    def _api_key_ref_is_a_secret_ref(cls, v: str) -> str:
        return _validate_api_key_ref(v)


class EmbeddingsConfig(BaseModel):
    """OpenAI embeddings config (used by KB indexer + retriever)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    model: str = "text-embedding-3-small"
    api_key_ref: str = "secrets/openai_key"

    @field_validator("api_key_ref")
    @classmethod
    def _api_key_ref_is_a_secret_ref(cls, v: str) -> str:
        return _validate_api_key_ref(v)


class LLMDefaults(BaseModel):
    """Tenant-level LLM defaults — Nodes inherit `default` unless they override."""

    model_config = ConfigDict(extra="forbid")

    default: LLMConfig
    classifier: LLMConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    cache_enabled: bool = True
