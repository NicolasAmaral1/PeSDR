"""Tests for the GuardrailsConfig + EmbeddingsConfig additions to tenant.yaml."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import ConsoleConfig, TenantConfig


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


def test_messaging_block_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.messaging is None


def test_messaging_whatsapp_cloud_full_block() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "secrets/wa_phone_id",
        "access_token_ref": "secrets/wa_token",
        "webhook_verify_token_ref": "secrets/wa_verify",
        "app_secret_ref": "secrets/wa_app_secret",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.messaging is not None
    assert cfg.messaging.provider == "whatsapp_cloud"
    assert cfg.messaging.phone_number_id_ref == "secrets/wa_phone_id"
    assert cfg.messaging.api_version == "v21.0"  # default


def test_messaging_whatsapp_cloud_requires_all_refs() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "secrets/wa_phone_id",
        # missing access_token_ref, webhook_verify_token_ref, app_secret_ref
    }
    with pytest.raises(ValidationError, match="access_token_ref"):
        TenantConfig.model_validate(data)


def test_messaging_secrets_prefix_enforced() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "wa_phone_id",  # missing secrets/ prefix
        "access_token_ref": "secrets/wa_token",
        "webhook_verify_token_ref": "secrets/wa_verify",
        "app_secret_ref": "secrets/wa_app_secret",
    }
    with pytest.raises(ValidationError, match=r"must start with 'secrets/'"):
        TenantConfig.model_validate(data)


def test_messaging_unknown_provider_allowed_at_schema_level() -> None:
    # provider is free-form str; factory dispatches. Schema only enforces
    # whatsapp_cloud-specific fields when provider == 'whatsapp_cloud'.
    data = _minimal_tenant_data()
    data["messaging"] = {"provider": "vialum_chat"}  # hypothetical future
    cfg = TenantConfig.model_validate(data)
    assert cfg.messaging is not None
    assert cfg.messaging.provider == "vialum_chat"
    assert cfg.messaging.phone_number_id_ref is None


def test_console_block_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.console is None


def test_console_disabled_by_default_when_block_present() -> None:
    data = _minimal_tenant_data()
    data["console"] = {}
    cfg = TenantConfig.model_validate(data)
    assert cfg.console is not None
    assert cfg.console.enabled is False


def test_console_enabled_true() -> None:
    data = _minimal_tenant_data()
    data["console"] = {"enabled": True}
    cfg = TenantConfig.model_validate(data)
    assert cfg.console is not None
    assert cfg.console.enabled is True


def test_console_rejects_extra_fields_for_forward_compat() -> None:
    """Spec keeps ConsoleConfig minimal — no per-tenant credentials in YAML.
    If someone tries the old `username`/`password_hash` shape, it must be
    rejected loudly (the right place is the users table)."""
    data = _minimal_tenant_data()
    data["console"] = {"enabled": True, "username": "joana", "password_hash": "x"}
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)
