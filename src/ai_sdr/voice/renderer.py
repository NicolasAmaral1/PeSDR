"""Outbound modality renderer — replaces the voice fallback slot in
flowengine.sender. decide_modality picks text vs voice per tenant policy;
render_and_send (Task 9) performs the synthesis + send.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Literal

from ai_sdr.flowengine.humanizer import HumanizationConfig, humanize
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.schemas.tenant_yaml import VoiceConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.voice.base import SpeechSynthesizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderResult:
    external_id: str | None
    modality: Literal["text", "voice"]
    media_type: str
    audio_url: str | None = None
    media_storage_key: str | None = None
    synthesis_voice_id: str | None = None
    voice_emotion: str | None = None
    audio_duration_ms: int | None = None
    synthesis_chars: int = 0


async def _send_text(
    messaging: MessagingAdapter,
    to: str,
    response_text: str,
    humanization: HumanizationConfig,
) -> str | None:
    chunks = humanize(response_text, humanization, is_voice=False)
    last_id: str | None = None
    for chunk in chunks:
        if chunk.delay_before_ms > 0:
            with contextlib.suppress(NotImplementedError, AttributeError):
                await messaging.mark_as_typing(to)
            await asyncio.sleep(chunk.delay_before_ms / 1000.0)
        out = await messaging.send_text(to, chunk.text)
        last_id = out.external_id
    return last_id


async def render_and_send(
    *,
    response_text: str,
    response_format: str | None,
    voice_emotion: str | None,
    to: str,
    message_id: str,
    voice_cfg: VoiceConfig | None,
    last_inbound_media_type: str,
    synthesizer: SpeechSynthesizer | None,
    storage: StorageAdapter | None,
    messaging: MessagingAdapter,
    humanization: HumanizationConfig,
) -> RenderResult:
    modality = (
        decide_modality(voice_cfg.response_mode, response_format, last_inbound_media_type)
        if voice_cfg is not None
        else "text"
    )

    if modality == "text" or voice_cfg is None or synthesizer is None or storage is None:
        last_id = await _send_text(messaging, to, response_text, humanization)
        return RenderResult(external_id=last_id, modality="text", media_type="text")

    assert voice_cfg.synthesis is not None  # guaranteed by VoiceConfig validator
    try:
        synth = await synthesizer.synthesize(
            response_text,
            voice_cfg.synthesis.voice_id,
            emotion=voice_emotion or voice_cfg.synthesis.default_emotion,
            fmt=voice_cfg.synthesis.format,
        )
        key = f"outbound/{message_id}.ogg"
        url = await storage.upload(key, synth.audio, synth.content_type)
        send_out = await messaging.send_audio(to, synth.audio, synth.content_type)
    except Exception as exc:
        if voice_cfg.fallback_to_text_on_failure:
            logger.warning("render.voice_failed_fallback_text msg=%s err=%s", message_id, exc)
            last_id = await _send_text(messaging, to, response_text, humanization)
            return RenderResult(external_id=last_id, modality="text", media_type="text")
        raise

    return RenderResult(
        external_id=send_out.external_id,
        modality="voice",
        media_type="audio",
        audio_url=url,
        media_storage_key=key,
        synthesis_voice_id=synth.voice_id,
        voice_emotion=voice_emotion,
        audio_duration_ms=synth.duration_ms,
        synthesis_chars=synth.char_count,
    )


def decide_modality(
    response_mode: str,
    response_format: str | None,
    last_inbound_media_type: str,
) -> Literal["text", "voice"]:
    if response_mode == "always":
        return "voice"
    if response_mode == "never":
        return "text"
    if response_mode == "match_lead":
        return "voice" if last_inbound_media_type == "audio" else "text"
    if response_mode == "context_driven":
        return "voice" if response_format in ("voice", "both") else "text"
    return "text"
