"""Factory dispatch tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.factory import build_messaging_adapter
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def test_factory_returns_fake_adapter() -> None:
    cfg = MessagingConfig(provider="fake")
    a = build_messaging_adapter(cfg, secrets={})
    assert isinstance(a, FakeMessagingAdapter)


def test_factory_unknown_provider_raises() -> None:
    cfg = MessagingConfig(provider="not_a_provider")
    with pytest.raises(ValueError, match="unknown messaging provider"):
        build_messaging_adapter(cfg, secrets={})


def test_factory_builds_whatsapp_cloud_with_secrets() -> None:
    """Stub registered in Task 12 — full impl arrives in Task 13."""
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "111",
        "wa_token": "EAA...",
        "wa_verify": "vt",
        "wa_app_secret": "as",
    }
    with pytest.raises(NotImplementedError, match="Task 13"):
        build_messaging_adapter(cfg, secrets=secrets)
