# tests/unit/test_normalizer.py
from __future__ import annotations

import pytest

from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechTranscriptionConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeTranscriber
from ai_sdr.voice.normalizer import normalize_inbound


class _Row:
    """Minimal stand-in for InboundMessageRow (attribute bag)."""

    def __init__(self, **kw):
        self.id = kw.get("id", "row-1")
        self.media_type = kw["media_type"]
        self.text = kw.get("text", "")
        self.raw = kw.get("raw", {})
        self.transcription = None
        self.transcription_confidence = None
        self.transcription_provider = None
        self.media_storage_key = None
        self.audio_url = None


def _cfg(min_conf=0.5):
    return SpeechTranscriptionConfig(provider="fake", credentials_ref="secrets/k", min_confidence=min_conf)


async def test_text_inbound_is_processed_untouched():
    row = _Row(media_type="text", text="oi")
    outcome = await normalize_inbound(
        row, messaging=FakeMessagingAdapter(), transcriber=FakeTranscriber(),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "processed"
    assert row.transcription is None


async def test_audio_inbound_transcribes_and_stores():
    messaging = FakeMessagingAdapter()
    messaging.stage_media("media-xyz", b"VOICE", "audio/ogg")
    row = _Row(media_type="audio", raw={"audio": {"id": "media-xyz"}})
    outcome = await normalize_inbound(
        row, messaging=messaging, transcriber=FakeTranscriber(text="quero saber o preço", confidence=0.9),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "processed"
    assert row.transcription == "quero saber o preço"
    assert row.transcription_confidence == 0.9
    assert row.transcription_provider == "fake"
    assert row.media_storage_key == "inbound/row-1.ogg"
    assert row.audio_url


async def test_low_confidence_returns_low_confidence():
    messaging = FakeMessagingAdapter()
    messaging.stage_media("m", b"V", "audio/ogg")
    row = _Row(media_type="audio", raw={"audio": {"id": "m"}})
    outcome = await normalize_inbound(
        row, messaging=messaging, transcriber=FakeTranscriber(text="??", confidence=0.2),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(min_conf=0.5),
    )
    assert outcome == "low_confidence"


async def test_download_failure_returns_unprocessable():
    row = _Row(media_type="audio", raw={"audio": {"id": "absent"}})
    outcome = await normalize_inbound(
        row, messaging=FakeMessagingAdapter(), transcriber=FakeTranscriber(),
        storage=FakeStorageAdapter(), transcription_cfg=_cfg(),
    )
    assert outcome == "unprocessable"
