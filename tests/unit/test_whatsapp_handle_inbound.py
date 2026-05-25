"""handle_inbound: HMAC verify + payload normalize. Uses real-shaped fixtures."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


def _adapter(app_secret: str = "test_app_secret") -> WhatsAppCloudAPIAdapter:
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
        "wa_verify": "vt",
        "wa_app_secret": app_secret,
    }
    return WhatsAppCloudAPIAdapter(cfg, secrets)


def _sign(body: bytes, secret: str = "test_app_secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def test_raises_signature_error_on_missing_header() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    with pytest.raises(SignatureError, match="missing"):
        await a.handle_inbound(body, headers={})


async def test_raises_signature_error_on_bad_signature() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    with pytest.raises(SignatureError, match="HMAC"):
        await a.handle_inbound(body, headers={"x-hub-signature-256": "sha256=" + "0" * 64})


async def test_parses_text_message() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert len(msgs) == 1
    m = msgs[0]
    assert m.external_id == "wamid.HBgM_FIRSTMESSAGE_AAAA="
    assert m.from_address == "+5511988887777"
    assert m.text == "oi, queria saber sobre a mentoria"
    assert m.received_at_iso.startswith("2025-")  # 1748169600 → 2025-05-25 (UTC)
    assert m.raw["id"] == "wamid.HBgM_FIRSTMESSAGE_AAAA="


async def test_ignores_status_update_payload() -> None:
    a = _adapter()
    body = _fixture("inbound_status_update.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert msgs == []


async def test_ignores_non_text_message() -> None:
    a = _adapter()
    body = _fixture("inbound_image.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert msgs == []  # image messages are skipped in Plano 5; Plano 8 picks them up


async def test_header_lookup_is_case_insensitive() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    # Some HTTP frameworks normalize headers to title-case
    msgs = await a.handle_inbound(body, headers={"X-Hub-Signature-256": _sign(body)})
    assert len(msgs) == 1
