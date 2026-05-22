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
