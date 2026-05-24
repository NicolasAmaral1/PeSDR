import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import TenantConfig


def test_minimal_tenant_yaml_validates() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "Joana Mentora",
        "timezone": "America/Sao_Paulo",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.id == "joana-mentora"
    assert cfg.timezone == "America/Sao_Paulo"


def test_full_tenant_yaml_validates() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "Joana Mentora",
        "timezone": "America/Sao_Paulo",
        "schedule": {
            "mon-fri": "08:00-22:00",
            "sat": "09:00-18:00",
            "sun": "off",
            "off_hours_behavior": "queue",
        },
        "conversation": {
            "debounce_ms": 5000,
            "optout_stop_words": ["para", "stop"],
            "optout_action": "end_conversation_silent",
        },
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.schedule is not None
    assert cfg.schedule.off_hours_behavior == "queue"
    assert cfg.conversation is not None
    assert cfg.conversation.debounce_ms == 5000
    assert "para" in cfg.conversation.optout_stop_words


def test_invalid_id_format_rejected() -> None:
    data = {
        "id": "Invalid ID With Spaces",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)


def test_invalid_off_hours_behavior_rejected() -> None:
    data = {
        "id": "x",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "schedule": {
            "mon-fri": "08:00-22:00",
            "off_hours_behavior": "fly_to_the_moon",
        },
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)


def test_tenant_yaml_accepts_llm_block() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "temperature": 0.7,
                "api_key_ref": "secrets/anthropic_key",
            },
            "classifier": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key_ref": "secrets/anthropic_key",
            },
        },
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm is not None
    assert cfg.llm.default.provider == "anthropic"
    assert cfg.llm.default.model == "claude-sonnet-4-6"
    assert cfg.llm.classifier is not None
    assert cfg.llm.classifier.model == "claude-haiku-4-5"


def test_llm_provider_accepts_arbitrary_string() -> None:
    """Plan 3 T2b: provider is free-form `str`; init_chat_model dispatches lazily."""
    data = {
        "id": "joana-mentora",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "google_genai",
                "model": "gemini-2.0-flash",
                "api_key_ref": "secrets/google_key",
            }
        },
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm is not None
    assert cfg.llm.default.provider == "google_genai"


def test_llm_provider_rejects_empty_string() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "",
                "model": "x",
                "api_key_ref": "secrets/x",
            }
        },
    }
    with pytest.raises(ValidationError, match="provider"):
        TenantConfig.model_validate(data)


def test_llm_api_key_ref_must_start_with_secrets_slash() -> None:
    data = {
        "id": "joana-mentora",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "sk-ant-PLAINTEXT-LEAK",
            }
        },
    }
    with pytest.raises(ValidationError, match="api_key_ref"):
        TenantConfig.model_validate(data)
