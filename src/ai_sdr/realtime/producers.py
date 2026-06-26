"""Realtime producer helpers — resolve instance + publish inbound events.

These helpers are called from the inbound webhook (and any future path that
needs to broadcast realtime events) after a DB commit.

BYPASSRLS note: the app DB role bypasses RLS, so every query here MUST
include an explicit tenant_id filter rather than relying on row-level security.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.instance import Instance
from ai_sdr.models.lead import Lead
from ai_sdr.realtime.events import publish_inbox_event

log = structlog.get_logger(__name__)


async def resolve_instance_id(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel_label: str,
) -> uuid.UUID | None:
    """Return the Instance.id for (tenant_id, channel_label), or None if not found.

    The explicit tenant_id filter is required because the app DB role bypasses
    RLS — do NOT rely on row-level security here.
    """
    result = await session.execute(
        select(Instance.id).where(
            Instance.tenant_id == tenant_id,
            Instance.channel_label == channel_label,
        )
    )
    return result.scalar_one_or_none()


async def publish_message_created(
    redis,
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lead: Lead,
    body_preview: str,
) -> None:
    """Publish a ``message.created`` and a ``contact.updated`` event for this lead.

    Resolves the instance via ``lead.inbound_channel_label``.  If no matching
    ``Instance`` row exists (e.g., the channel was not yet configured), the
    function logs a warning and returns without raising.

    Args:
        redis: A ``redis.asyncio.Redis`` client (decode_responses=True).
        session: SQLAlchemy async session (already in tenant context).
        tenant_id: Tenant UUID — used for the explicit tenant filter in the
            instance lookup (BYPASSRLS policy, no RLS reliance).
        lead: The Lead ORM object whose channel originated the message.
        body_preview: Short preview of the inbound text (caller truncates).
    """
    instance_id = await resolve_instance_id(
        session,
        tenant_id=tenant_id,
        channel_label=lead.inbound_channel_label,
    )
    if instance_id is None:
        log.warning(
            "realtime.no_instance",
            tenant_id=str(tenant_id),
            channel_label=lead.inbound_channel_label,
            lead_id=str(lead.id),
        )
        return

    lead_id_str = str(lead.id)

    await publish_inbox_event(
        redis,
        instance_id=instance_id,
        type="message.created",
        lead_id=lead.id,
        payload={"lead_id": lead_id_str, "preview": body_preview},
    )

    await publish_inbox_event(
        redis,
        instance_id=instance_id,
        type="contact.updated",
        lead_id=lead.id,
        payload={"lead_id": lead_id_str},
    )
