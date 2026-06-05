"""Outbound send for FlowEngine v2.

Wraps the existing MessagingAdapter.send_text. Voice paths log a
warning and fall back to text — FE-05 implements VoiceAdapter.

Chunking / humanization is intentionally absent in FE-01b. FE-03 adds
the humanization post-processor that splits + delays chunks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_sdr.flowengine.decision import TurnDecision
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
) -> SendResult:
    """Send the assistant response as a single text message."""
    if decision.response_format in ("voice", "both"):
        logger.warning(
            "voice_format_not_implemented_fe01b lead_id=%s format=%s — "
            "falling back to text",
            lead.id,
            decision.response_format,
        )

    result = await adapter.send_text(lead.whatsapp_e164, decision.response_text)
    return SendResult(
        external_id=result.external_id,
        status="sent",
        error_detail=None,
    )
