from __future__ import annotations

import httpx
import pytest

from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, SpeechTranscriptionConfig
from ai_sdr.voice import elevenlabs as el
from ai_sdr.voice.base import SpeechSynthesizer
from ai_sdr.voice.fake import FakeSynthesizer

pytestmark = pytest.mark.integration


def _mock_client(response: httpx.Response):
    return lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: response), timeout=timeout
    )


@pytest.fixture(params=["fake", "elevenlabs"])
def synth_under_test(request, monkeypatch) -> SpeechSynthesizer:
    if request.param == "fake":
        return FakeSynthesizer()
    cfg = SpeechSynthesisConfig(
        provider="elevenlabs", credentials_ref="secrets/k", voice_id="v"
    )
    monkeypatch.setattr(el, "_build_http_client", _mock_client(httpx.Response(200, content=b"OGGDATA")))
    return el.ElevenLabsSynthesizer(cfg, {"k": "xi-key"})


async def test_synthesize_returns_audio_bytes(synth_under_test):
    r = await synth_under_test.synthesize("oi", "v")
    assert r.audio
    assert r.char_count == 2


async def test_elevenlabs_transcriber_parses_text(monkeypatch):
    cfg = SpeechTranscriptionConfig(provider="elevenlabs", credentials_ref="secrets/k")
    monkeypatch.setattr(
        el, "_build_http_client",
        _mock_client(httpx.Response(200, json={"text": "oi tudo bem", "language_probability": 0.9})),
    )
    t = el.ElevenLabsTranscriber(cfg, {"k": "xi-key"})
    r = await t.transcribe(b"\x00")
    assert r.text == "oi tudo bem"
    assert r.confidence == 0.9
    assert r.provider == "elevenlabs"
