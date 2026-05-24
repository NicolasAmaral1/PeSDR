"""Tests for the GuardrailsConfig + EmbeddingsConfig additions to tenant.yaml."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import TenantConfig


def _minimal_tenant_data() -> dict:
    return {
        "id": "example",
        "display_name": "Example",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "secrets/anthropic_key",
            }
        },
    }


def test_guardrails_block_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.guardrails is None


def test_guardrails_disabled_allows_empty_lists() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": False,
        "allowed_prices": [],
        "allowed_products": [],
        "fallback_text": "Confirmo já já, ok?",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.guardrails is not None
    assert cfg.guardrails.enabled is False


def test_guardrails_enabled_requires_at_least_one_list() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [],
        "allowed_products": [],
        "fallback_text": "Confirmo já já, ok?",
    }
    with pytest.raises(ValidationError, match="allowed_prices"):
        TenantConfig.model_validate(data)


def test_guardrails_fallback_text_min_length() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [247],
        "allowed_products": [],
        "fallback_text": "ok",  # too short
    }
    with pytest.raises(ValidationError, match="fallback_text"):
        TenantConfig.model_validate(data)


def test_guardrails_full_block() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [247, 1497, 1997, 2000, 6000],
        "allowed_products": ["Mentoria", "Aceleradora", "Downsell"],
        "critic_enabled": True,
        "fallback_text": "Deixa eu confirmar e já te respondo, ok?",
        "max_retries": 2,
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.guardrails is not None
    assert cfg.guardrails.allowed_prices == [247, 1497, 1997, 2000, 6000]
    assert cfg.guardrails.critic_enabled is True
    assert cfg.guardrails.max_retries == 2


def test_guardrails_enabled_with_only_products_passes() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [],
        "allowed_products": ["Mentoria"],
        "fallback_text": "Confirmo já já, ok?",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.guardrails is not None
    assert cfg.guardrails.allowed_products == ["Mentoria"]


def test_llm_defaults_cache_enabled_default_true() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.llm.cache_enabled is True


def test_llm_defaults_cache_enabled_can_be_disabled() -> None:
    data = _minimal_tenant_data()
    data["llm"]["cache_enabled"] = False
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.cache_enabled is False


def test_llm_embeddings_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.llm.embeddings is None


def test_llm_embeddings_defaults() -> None:
    data = _minimal_tenant_data()
    data["llm"]["embeddings"] = {"provider": "openai"}
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.embeddings is not None
    assert cfg.llm.embeddings.provider == "openai"
    assert cfg.llm.embeddings.model == "text-embedding-3-small"
    assert cfg.llm.embeddings.api_key_ref == "secrets/openai_key"


def test_llm_embeddings_explicit_values() -> None:
    data = _minimal_tenant_data()
    data["llm"]["embeddings"] = {
        "provider": "openai",
        "model": "text-embedding-3-large",
        "api_key_ref": "secrets/openai_key_alt",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.embeddings is not None
    assert cfg.llm.embeddings.model == "text-embedding-3-large"
    assert cfg.llm.embeddings.api_key_ref == "secrets/openai_key_alt"
