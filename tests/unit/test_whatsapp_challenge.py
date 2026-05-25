"""verification_challenge: WhatsApp's hub.mode=subscribe handshake."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def _adapter(verify_token: str = "vt_secret") -> WhatsAppCloudAPIAdapter:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999111",
        "wa_token": "EAA...",
        "wa_verify": verify_token,
        "wa_app_secret": "as",
    }
    return WhatsAppCloudAPIAdapter(cfg, secrets)


def test_challenge_echoes_when_token_matches() -> None:
    a = _adapter("vt_secret")
    out = a.verification_challenge(
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "vt_secret",
            "hub.challenge": "abc123",
        }
    )
    assert out == "abc123"


def test_challenge_returns_none_when_mode_not_subscribe() -> None:
    a = _adapter("vt_secret")
    out = a.verification_challenge(
        {
            "hub.mode": "something_else",
            "hub.verify_token": "vt_secret",
            "hub.challenge": "abc123",
        }
    )
    assert out is None


def test_challenge_raises_when_token_mismatch() -> None:
    a = _adapter("vt_secret")
    with pytest.raises(SignatureError, match="verify token"):
        a.verification_challenge(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG",
                "hub.challenge": "abc123",
            }
        )
