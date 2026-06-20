"""Inbound modality normalizer — runs in the worker BEFORE run_turn.

Translates an inbound audio message into text the text-only turn can
consume: download media → archive to storage → transcribe. Text inbound
is a passthrough. The row is mutated in place; the caller commits.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.schemas.tenant_yaml import SpeechTranscriptionConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.voice.base import SpeechTranscriber

logger = logging.getLogger(__name__)

NormalizeOutcome = Literal["processed", "low_confidence", "unprocessable"]


class _InboundRowProto(Protocol):
    id: object
    media_type: str
    raw: dict
    transcription: str | None
    transcription_confidence: float | None
    transcription_provider: str | None
    media_storage_key: str | None
    audio_url: str | None


async def normalize_inbound(
    inbound: _InboundRowProto,
    *,
    messaging: MessagingAdapter,
    transcriber: SpeechTranscriber,
    storage: StorageAdapter,
    transcription_cfg: SpeechTranscriptionConfig,
) -> NormalizeOutcome:
    if inbound.media_type != "audio":
        return "processed"

    media_ref = (inbound.raw.get("audio") or {}).get("id")
    if not media_ref:
        logger.warning("normalize_inbound.no_media_ref inbound=%s", inbound.id)
        return "unprocessable"

    try:
        audio, content_type = await messaging.download_media(media_ref)
    except Exception as exc:  # CDN expiry / transient — treat as unprocessable
        logger.warning("normalize_inbound.download_failed inbound=%s err=%s", inbound.id, exc)
        return "unprocessable"

    key = f"inbound/{inbound.id}.ogg"
    try:
        url = await storage.upload(key, audio, content_type)
        inbound.media_storage_key = key
        inbound.audio_url = url
    except Exception as exc:  # archive miss must not block the turn
        logger.warning("normalize_inbound.storage_failed inbound=%s err=%s", inbound.id, exc)

    result = await transcriber.transcribe(audio, language=transcription_cfg.language)
    inbound.transcription = result.text
    inbound.transcription_confidence = result.confidence
    inbound.transcription_provider = result.provider

    if result.confidence < transcription_cfg.min_confidence:
        return "low_confidence"
    return "processed"
