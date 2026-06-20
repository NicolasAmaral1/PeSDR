from __future__ import annotations

import pytest

from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, VoiceConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer
from ai_sdr.voice.renderer import render_and_send


def _voice_cfg(mode="always", fallback=True) -> VoiceConfig:
    return VoiceConfig(
        response_mode=mode,
        fallback_to_text_on_failure=fallback,
        synthesis=SpeechSynthesisConfig(provider="fake", credentials_ref="secrets/k", voice_id="v1"),
    )


async def test_text_path_sends_text_and_no_audio():
    messaging = FakeMessagingAdapter()
    r = await render_and_send(
        response_text="olá", response_format=None, voice_emotion=None,
        to="+5511", message_id="m1", voice_cfg=None, last_inbound_media_type="text",
        synthesizer=None, storage=None, messaging=messaging,
        humanization=HumanizationConfig(enabled=False),
    )
    assert r.modality == "text"
    assert messaging.sent_messages and not messaging.sent_audio


async def test_voice_path_synthesizes_stores_and_sends_audio():
    messaging = FakeMessagingAdapter()
    storage = FakeStorageAdapter()
    r = await render_and_send(
        response_text="bom dia", response_format=None, voice_emotion="happy",
        to="+5511", message_id="out-1", voice_cfg=_voice_cfg(), last_inbound_media_type="audio",
        synthesizer=FakeSynthesizer(), storage=storage, messaging=messaging,
        humanization=HumanizationConfig(),
    )
    assert r.modality == "voice"
    assert r.media_type == "audio"
    assert r.synthesis_voice_id == "v1"
    assert r.voice_emotion == "happy"
    assert r.synthesis_chars == len("bom dia")
    assert messaging.sent_audio and not messaging.sent_messages
    assert storage.objects["outbound/out-1.ogg"]
    assert r.audio_url


async def test_synthesis_failure_falls_back_to_text():
    class _BoomSynth(FakeSynthesizer):
        async def synthesize(self, *a, **k):
            raise RuntimeError("eleven down")

    messaging = FakeMessagingAdapter()
    r = await render_and_send(
        response_text="oi", response_format=None, voice_emotion=None,
        to="+5511", message_id="m2", voice_cfg=_voice_cfg(fallback=True),
        last_inbound_media_type="audio", synthesizer=_BoomSynth(),
        storage=FakeStorageAdapter(), messaging=messaging, humanization=HumanizationConfig(),
    )
    assert r.modality == "text"
    assert messaging.sent_messages and not messaging.sent_audio


async def test_synthesis_failure_without_fallback_raises():
    class _BoomSynth(FakeSynthesizer):
        async def synthesize(self, *a, **k):
            raise RuntimeError("eleven down")

    with pytest.raises(RuntimeError):
        await render_and_send(
            response_text="oi", response_format=None, voice_emotion=None,
            to="+5511", message_id="m3", voice_cfg=_voice_cfg(fallback=False),
            last_inbound_media_type="audio", synthesizer=_BoomSynth(),
            storage=FakeStorageAdapter(), messaging=FakeMessagingAdapter(),
            humanization=HumanizationConfig(),
        )
