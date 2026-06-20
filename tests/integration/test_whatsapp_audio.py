# tests/integration/test_whatsapp_audio.py
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

pytestmark = pytest.mark.integration


def _adapter() -> WhatsAppCloudAPIAdapter:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/p",
        access_token_ref="secrets/t",
        webhook_verify_token_ref="secrets/v",
        app_secret_ref="secrets/s",
    )
    return WhatsAppCloudAPIAdapter(cfg, {"p": "111", "t": "TOK", "v": "vt", "s": "appsecret"})


def _sign(body: bytes, secret: str = "appsecret") -> dict:
    return {"x-hub-signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


async def test_handle_inbound_audio_yields_media_ref():
    a = _adapter()
    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "id": "wamid.AUDIO1", "from": "5511988887777", "timestamp": "1716638400",
            "type": "audio", "audio": {"id": "media-xyz", "mime_type": "audio/ogg"},
        }]}}]}]
    }
    body = json.dumps(payload).encode()
    msgs = await a.handle_inbound(body, _sign(body))
    assert len(msgs) == 1
    assert msgs[0].media_type == "audio"
    assert msgs[0].media_ref == "media-xyz"
    assert msgs[0].text == ""


async def test_send_audio_uploads_then_sends(monkeypatch):
    a = _adapter()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "uploaded-media-1"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.AUDOUT"}]})

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15.0),
    )
    r = await a.send_audio("+5511988887777", b"OGGBYTES", "audio/ogg")
    assert r.external_id == "wamid.AUDOUT"


async def test_download_media_two_step(monkeypatch):
    a = _adapter()

    def handler(req: httpx.Request) -> httpx.Response:
        if "/media-xyz" in req.url.path or req.url.path.endswith("media-xyz"):
            return httpx.Response(200, json={"url": "https://lookaside.fbcdn.net/blob", "mime_type": "audio/ogg"})
        return httpx.Response(200, content=b"VOICE")

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=15.0),
    )
    data, mime = await a.download_media("media-xyz")
    assert data == b"VOICE"
    assert mime == "audio/ogg"
