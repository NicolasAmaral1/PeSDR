"""Audit helpers — insert one OutboundMessage row per adapter send.

Worker (Plan 5 + 9 paths) and scanner (Plan 9) call these immediately
after the adapter call returns (success or raise → except). Helper
only flushes; the caller commits as part of its own transaction so
the audit row goes with the rest of the state updates (msg.status,
talkflow timestamps, follow_up_job mutations).

Known race (spec §7): if the caller's commit fails AFTER the adapter
already sent the message, the audit row is lost. The caller is
expected to emit a warning log identifying the external_id and the
unrecorded send. The Meta message is not retried (would double-send).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.outbound_message import OutboundMessage

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant


MessageType = Literal["text", "template"]
TriggeredBy = Literal["inbound", "follow_up_scanner", "window_expired_recovery"]


async def record_outbound_sent(
    session: AsyncSession,
    *,
    tenant: "Tenant",
    talkflow: "TalkFlow",
    lead: "Lead",
    provider: str,
    message_type: MessageType,
    triggered_by: TriggeredBy,
    sent_at: datetime,
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    external_id: str | None = None,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a successful send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        provider=provider,
        message_type=message_type,
        body_text=body_text,
        template_ref=template_ref,
        template_language=template_language,
        template_params=template_params,
        status="sent",
        external_id=external_id,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row


async def record_outbound_failed(
    session: AsyncSession,
    *,
    tenant: "Tenant",
    talkflow: "TalkFlow",
    lead: "Lead",
    provider: str,
    message_type: MessageType,
    triggered_by: TriggeredBy,
    error_detail: str,
    sent_at: datetime,
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a failed send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        provider=provider,
        message_type=message_type,
        body_text=body_text,
        template_ref=template_ref,
        template_language=template_language,
        template_params=template_params,
        status="failed",
        error_detail=error_detail,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row
