from __future__ import annotations

from ai_sdr.voice.base import SynthesisResult, TranscriptionResult
from ai_sdr.voice.fake import FakeSynthesizer, FakeTranscriber


async def test_fake_synthesizer_returns_bytes_and_char_count():
    s = FakeSynthesizer()
    r = await s.synthesize("olá mundo", "voice-1")
    assert isinstance(r, SynthesisResult)
    assert r.audio  # non-empty bytes
    assert r.voice_id == "voice-1"
    assert r.char_count == len("olá mundo")


async def test_fake_transcriber_echoes_scripted_text_with_confidence():
    t = FakeTranscriber(text="oi tudo bem", confidence=0.92)
    r = await t.transcribe(b"\x00\x01")
    assert isinstance(r, TranscriptionResult)
    assert r.text == "oi tudo bem"
    assert r.confidence == 0.92
    assert r.provider == "fake"
