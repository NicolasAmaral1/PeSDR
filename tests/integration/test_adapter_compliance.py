"""Adapter-compliance suite — runs identical contract tests against every
MessagingAdapter impl. To add a new impl, append its key to the
`@pytest.fixture(params=[...])` below and provide a builder.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult
from ai_sdr.messaging.errors import (
    AuthError,
    RecipientUnreachable,
    SignatureError,
    TransientError,
)
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"

pytestmark = pytest.mark.integration


def _build_whatsapp_mocked(monkeypatch) -> tuple[MessagingAdapter, dict]:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999", "wa_token": "EAA",
        "wa_verify": "vt", "wa_app_secret": "appsecret",
    }
    adapter = WhatsAppCloudAPIAdapter(cfg, secrets)
    import tenacity
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY", tenacity.wait_none()
    )
    helpers = {
        "app_secret": "appsecret",
        "build_inbound_body": lambda: (FIXTURES / "inbound_text.json").read_bytes(),
        "expected_external_id": "wamid.HBgM_FIRSTMESSAGE_AAAA=",
        "expected_from_address": "+5511988887777",
    }
    return adapter, helpers


def _build_fake() -> tuple[MessagingAdapter, dict]:
    adapter = FakeMessagingAdapter()
    msg = InboundMessage(
        external_id="fake_ext_1",
        from_address="+5511988887777",
        text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00",
        raw={"id": "fake_ext_1"},
    )
    adapter.queue_inbound(msg)
    helpers = {
        "app_secret": None,
        "build_inbound_body": lambda: b"",
        "expected_external_id": "fake_ext_1",
        "expected_from_address": "+5511988887777",
    }
    return adapter, helpers


@pytest.fixture(params=["fake", "whatsapp_cloud_mocked"])
def adapter_under_test(request, monkeypatch) -> tuple[MessagingAdapter, dict]:
    if request.param == "fake":
        return _build_fake()
    return _build_whatsapp_mocked(monkeypatch)


def _sign(body: bytes, secret: str | None) -> dict:
    if secret is None:
        return {}
    return {
        "x-hub-signature-256": "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
    }


async def test_handle_inbound_returns_normalized_message(adapter_under_test) -> None:
    adapter, helpers = adapter_under_test
    body = helpers["build_inbound_body"]()
    headers = _sign(body, helpers["app_secret"])
    msgs = await adapter.handle_inbound(body, headers)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.external_id == helpers["expected_external_id"]
    assert m.from_address == helpers["expected_from_address"]
    assert m.text != ""


async def test_handle_inbound_raises_signature_error_on_tampered_payload(
    adapter_under_test,
) -> None:
    adapter, helpers = adapter_under_test
    if helpers["app_secret"] is None:
        pytest.skip("fake adapter does not enforce HMAC")
    body = helpers["build_inbound_body"]()
    with pytest.raises(SignatureError):
        await adapter.handle_inbound(
            body, headers={"x-hub-signature-256": "sha256=" + "0" * 64}
        )


async def test_send_text_returns_external_id(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, WhatsAppCloudAPIAdapter):
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200, json={"messages": [{"id": "wamid.OUT_X="}]}
                    )
                ),
                timeout=15.0,
            ),
        )
    r = await adapter.send_text("+5511988887777", "hi")
    assert isinstance(r, SendResult)
    assert r.external_id


async def test_send_text_raises_recipient_unreachable(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, FakeMessagingAdapter):
        adapter.fail_next_send(RecipientUnreachable("number not on WA"))
    else:
        # WhatsApp mocked: 400/131026
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        400, json={"error": {"code": 131026, "message": "not on WA"}}
                    )
                ),
                timeout=15.0,
            ),
        )
    with pytest.raises(RecipientUnreachable):
        await adapter.send_text("+5511988887777", "hi")


def test_verification_challenge_signature(adapter_under_test) -> None:
    """All adapters must expose verification_challenge — either echo or None."""
    adapter, _ = adapter_under_test
    out = adapter.verification_challenge({})
    assert out is None or isinstance(out, str)
