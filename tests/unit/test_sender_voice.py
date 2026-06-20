"""Unit tests for send_response_text voice path (Task 11).

Tests that sender delegates to render_and_send when voice deps are provided,
and keeps the text-only path byte-identical when voice_cfg=None.
"""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.flowengine.sender import send_response_text
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechSynthesisConfig, VoiceConfig
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.voice.fake import FakeSynthesizer


class _Lead:
    id = "lead-1"
    whatsapp_e164 = "+5511988887777"


def _decision(fmt=None) -> TurnDecision:
    return TurnDecision(
        response_text="bom dia", response_format=fmt, collected_fields={}, reasoning="x",
    )


@pytest.mark.asyncio
async def test_sender_text_only_when_no_voice_cfg():
    adapter = FakeMessagingAdapter()
    r = await send_response_text(
        adapter=adapter, lead=_Lead(), decision=_decision(),
        humanization_config=HumanizationConfig(enabled=False),
    )
    assert r.media_type == "text"
    assert adapter.sent_messages and not adapter.sent_audio


@pytest.mark.asyncio
async def test_sender_voice_when_always_mode():
    adapter = FakeMessagingAdapter()
    vcfg = VoiceConfig(
        response_mode="always",
        synthesis=SpeechSynthesisConfig(provider="fake", credentials_ref="secrets/k", voice_id="v1"),
    )
    r = await send_response_text(
        adapter=adapter, lead=_Lead(), decision=_decision(),
        humanization_config=HumanizationConfig(), voice_cfg=vcfg,
        synthesizer=FakeSynthesizer(), storage=FakeStorageAdapter(),
        last_inbound_media_type="text",
    )
    assert r.media_type == "audio"
    assert r.synthesis_voice_id == "v1"
    assert adapter.sent_audio
