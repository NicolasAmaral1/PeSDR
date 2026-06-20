"""Outbound send for FlowEngine v2.

Humanization (chunking + typing indicator + delays) is owned by the
Outbound Renderer (ai_sdr.voice.renderer). Voice paths delegate to
render_and_send (FE-05 Task 9); text-only turns go through the same
function with voice_cfg=None so the code path is unified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.models.lead import Lead

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Normalized send outcome — decoupled from MessagingAdapter's SendResult.

    status is always "sent" on success (adapter raises on failure so we
    never reach this point with a terminal error).

    Media fields are populated only when a voice/audio path was taken;
    they default to the text-path no-op values so callers don't need to
    branch on None for every field.
    """

    external_id: str | None
    status: str
    error_detail: str | None = None
    media_type: str = "text"
    audio_url: str | None = None
    media_storage_key: str | None = None
    synthesis_voice_id: str | None = None
    voice_emotion: str | None = None
    audio_duration_ms: int | None = None
    synthesis_chars: int = 0


async def send_response_text(
    *,
    adapter: MessagingAdapter,
    lead: Lead,
    decision: TurnDecision,
    humanization_config: HumanizationConfig,
    voice_cfg=None,
    synthesizer=None,
    storage=None,
    last_inbound_media_type: str = "text",
    message_id: str | None = None,
) -> SendResult:
    """Send the assistant response, delegating to render_and_send.

    When voice_cfg is None the text path is taken — byte-identical to
    the pre-FE-05 behaviour. When voice deps are present the renderer
    decides the modality (text vs audio) and returns a RenderResult
    that is mapped to SendResult.

    message_id: deterministic storage key seed (`outbound/{id}.ogg`).
    Defaults to str(lead.id) when not provided (unit tests without an
    inbound). The pipeline always passes str(inbound.id).
    """
    from ai_sdr.voice.renderer import render_and_send

    resolved_message_id = message_id if message_id is not None else str(lead.id)

    render = await render_and_send(
        response_text=decision.response_text,
        response_format=decision.response_format,
        voice_emotion=decision.voice_emotion,
        to=lead.whatsapp_e164,
        message_id=resolved_message_id,
        voice_cfg=voice_cfg,
        last_inbound_media_type=last_inbound_media_type,
        synthesizer=synthesizer,
        storage=storage,
        messaging=adapter,
        humanization=humanization_config,
    )

    return SendResult(
        external_id=render.external_id,
        status="sent",
        media_type=render.media_type,
        audio_url=render.audio_url,
        media_storage_key=render.media_storage_key,
        synthesis_voice_id=render.synthesis_voice_id,
        voice_emotion=render.voice_emotion,
        audio_duration_ms=render.audio_duration_ms,
        synthesis_chars=render.synthesis_chars,
    )
