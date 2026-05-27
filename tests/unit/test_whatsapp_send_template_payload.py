"""WhatsAppCloudAPIAdapter.send_template — payload shape + error classification."""

from __future__ import annotations

import httpx
import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RecipientUnreachable,
    TransientError,
)
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@pytest.fixture
def adapter_no_retry_sleep(monkeypatch) -> WhatsAppCloudAPIAdapter:
    import tenacity

    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "PNID",
        "wa_token": "TOKEN",
        "wa_verify": "vt",
        "wa_app_secret": "as",
    }
    a = WhatsAppCloudAPIAdapter(cfg, secrets)
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY", tenacity.wait_none()
    )
    return a


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+5511999", "wa_id": "5511999"}],
            "messages": [{"id": "wamid.TPL_OUT="}],
        },
    )


def _error_response(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "err"}})


async def test_payload_shape_with_params(adapter_no_retry_sleep, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content)
        return _ok_response()

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_template(
        to="+5511999",
        template_ref="followup_24h_v1",
        language="pt_BR",
        params=["Maria", "mentoria"],
    )
    assert r.external_id == "wamid.TPL_OUT="
    assert "/PNID/messages" in captured["url"]
    body = captured["body"]
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "5511999"  # no + prefix per Meta API
    assert body["type"] == "template"
    assert body["template"]["name"] == "followup_24h_v1"
    assert body["template"]["language"]["code"] == "pt_BR"
    assert body["template"]["components"][0]["type"] == "body"
    assert body["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Maria"},
        {"type": "text", "text": "mentoria"},
    ]


async def test_payload_shape_without_params(adapter_no_retry_sleep, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return _ok_response()

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    await adapter_no_retry_sleep.send_template(
        to="+5511999", template_ref="x", language="pt_BR", params=[],
    )
    # When params is empty, components MUST be omitted (Meta API rejects empty components)
    assert "components" not in captured["body"]["template"]


@pytest.mark.parametrize(
    "status, code, expected_exc",
    [
        (401, 190, AuthError),
        (400, 131026, RecipientUnreachable),
        (400, 131049, PolicyError),
        (503, 2, TransientError),
    ],
)
async def test_classifies_errors_same_as_send_text(
    adapter_no_retry_sleep, monkeypatch, status, code, expected_exc
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: _error_response(status, code)),
            timeout=15.0,
        ),
    )
    with pytest.raises(expected_exc):
        await adapter_no_retry_sleep.send_template(
            "+5511999", "x", "pt_BR", [],
        )
