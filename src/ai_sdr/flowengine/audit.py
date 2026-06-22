"""OutboundMessage audit for FlowEngine v2 turns.

Idempotency key shape (per resolved design decision):
  f"{tenant_id}:{talk_id}:{turn_index}:{chunk_index}"

FE-01b emits one chunk per turn, so chunk_index=0 always. FE-03
humanization extends to multiple chunks per turn forward-compatibly.

The OutboundMessage table (P10) does not have a dedicated
idempotency_key column. We dedupe by (talkflow_id=talk.id, turn_index),
where turn_index is encoded in template_params for queryability.

Migration 0024 dropped the FK from outbound_messages.talkflow_id to
talkflows.id so we can reuse the column for the new talks.id during the
v1->v2 coexistence window. FE-02 cleans this up properly by adding a
talk_id column + migrating rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.sender import SendResult
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talk import Talk


async def record_outbound_audit(
    session: AsyncSession,
    *,
    talk: Talk,
    inbound: InboundMessageRow,
    response_text: str,
    turn_index: int,
    send_result: SendResult,
    provider: str,
    sent_at: datetime,
    chunk_index: int = 0,
    media_type: str = "text",
    audio_url: str | None = None,
    media_storage_key: str | None = None,
    synthesis_voice_id: str | None = None,
    voice_emotion: str | None = None,
    audio_duration_ms: int | None = None,
) -> OutboundMessage | None:
    """Insert one OutboundMessage row (idempotent by (talk, turn, chunk))."""
    existing = (
        await session.execute(
            select(OutboundMessage).where(
                OutboundMessage.tenant_id == talk.tenant_id,
                OutboundMessage.talkflow_id == talk.id,
                OutboundMessage.template_params.op("->>")("turn_index") == str(turn_index),
                OutboundMessage.template_params.op("->>")("chunk_index") == str(chunk_index),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = OutboundMessage(
        tenant_id=talk.tenant_id,
        talkflow_id=talk.id,
        lead_id=talk.lead_id,
        provider=provider,
        message_type=("audio" if media_type == "audio" else "text"),
        body_text=response_text,
        template_ref=None,
        template_language=None,
        template_params={"turn_index": turn_index, "chunk_index": chunk_index},
        status=send_result.status,
        external_id=send_result.external_id,
        error_detail=send_result.error_detail,
        triggered_by="inbound",
        inbound_message_id=inbound.id,
        follow_up_job_id=None,
        sent_at=sent_at,
        media_type=media_type,
        media_storage_key=media_storage_key,
        audio_url=audio_url,
        audio_duration_ms=audio_duration_ms,
        synthesis_voice_id=synthesis_voice_id,
        voice_emotion=voice_emotion,
    )
    session.add(row)
    return row
