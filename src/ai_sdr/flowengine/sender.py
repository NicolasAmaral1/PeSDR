"""Outbound send for FlowEngine v2.

Humanization (chunking + typing indicator + delays) lands in FE-03b.
The function is split-aware: humanizer returns list[Chunk] and we
iterate. Voice paths still fall back to text (FE-05 implements VoiceAdapter).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig, humanize
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.models.lead import Lead

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Normalized send outcome — decoupled from MessagingAdapter's SendResult.

    status is always "sent" on success (adapter raises on failure so we
    never reach this point with a terminal error).
    """

    external_id: str | None
    status: str
    error_detail: str | None = None


async def send_response_text(
    *,
    adapter: MessagingAdapter,
    lead: Lead,
    decision: TurnDecision,
    humanization_config: HumanizationConfig,
) -> SendResult:
    """Send the assistant response as one or more humanized chunks.

    Voice mode falls back to text and logs a warning (FE-05 will wire the
    real VoiceAdapter).
    """
    if decision.response_format in ("voice", "both"):
        logger.warning(
            "voice_format_not_implemented_fe03b lead_id=%s format=%s — falling back to text",
            lead.id,
            decision.response_format,
        )

    chunks = humanize(
        decision.response_text,
        humanization_config,
        is_voice=(decision.response_format == "voice"),
    )

    if not chunks:
        logger.warning(
            "humanize_returned_empty_chunks lead_id=%s text_len=%d",
            lead.id,
            len(decision.response_text),
        )
        return SendResult(external_id=None, status="sent", error_detail=None)

    last_external_id: str | None = None
    for chunk in chunks:
        if chunk.delay_before_ms > 0:
            with contextlib.suppress(NotImplementedError, AttributeError):
                await adapter.mark_as_typing(lead.whatsapp_e164)
            await asyncio.sleep(chunk.delay_before_ms / 1000.0)

        send_outcome = await adapter.send_text(lead.whatsapp_e164, chunk.text)
        last_external_id = send_outcome.external_id

    logger.info(
        "humanization.chunks_emitted lead_id=%s chunk_count=%d total_chars=%d",
        lead.id,
        len(chunks),
        sum(len(c.text) for c in chunks),
    )

    return SendResult(
        external_id=last_external_id,
        status="sent",
        error_detail=None,
    )
