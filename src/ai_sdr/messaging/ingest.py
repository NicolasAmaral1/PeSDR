"""Persist inbound messages + resolve sender to a Lead.

Both helpers leave commit() to the caller; they only flush. Tenant context
must be set by the caller via set_tenant_context() — these helpers do not
escalate privileges.

The provider-dispatching find_or_create_lead_by_address() is Plano 5's
ad-hoc Identity boundary. Plano 6 promotes it to an `IdentityResolver`
interface and adds a Vialum impl; the WhatsApp behavior here becomes the
default 'InternalLead' implementation — no signature change at the call
sites.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant


@dataclass(frozen=True)
class IngestResult:
    status: Literal["queued", "skipped_dedupe"]
    lead_id: uuid.UUID


async def find_or_create_lead_by_address(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    provider: str,
    address: str,
) -> Lead:
    """Return the lead with this provider-native address, creating one with
    status='pending_assignment' if needed.

    Plano 5 only supports `whatsapp_cloud` — the address is stored in
    `leads.whatsapp_e164`. Other providers raise NotImplementedError until
    Plano 6 generalizes the identity layer."""
    if provider != "whatsapp_cloud":
        raise NotImplementedError(
            f"find_or_create_lead_by_address: provider {provider!r} "
            "is not supported until Plano 6 (Identity)"
        )

    existing = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.whatsapp_e164 == address,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    lead = Lead(
        tenant_id=tenant_id,
        whatsapp_e164=address,
        status="pending_assignment",
    )
    session.add(lead)
    await session.flush()
    return lead


async def ingest_inbound_message(
    session: AsyncSession,
    tenant: Tenant,
    provider: str,
    msg: InboundMessage,
) -> IngestResult:
    """Resolve sender → lead, then INSERT ... ON CONFLICT DO NOTHING the
    inbound row. Returns IngestResult so the caller knows whether to
    enqueue work or not."""
    lead = await find_or_create_lead_by_address(session, tenant.id, provider, msg.from_address)

    received_at = datetime.fromisoformat(msg.received_at_iso)
    stmt = (
        pg_insert(InboundMessageRow)
        .values(
            tenant_id=tenant.id,
            provider=provider,
            external_id=msg.external_id,
            lead_id=lead.id,
            from_address=msg.from_address,
            text=msg.text,
            received_at=received_at,
            raw=dict(msg.raw),
            status="queued",
            media_type=msg.media_type,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "provider", "external_id"])
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:  # type: ignore[attr-defined]
        return IngestResult(status="skipped_dedupe", lead_id=lead.id)
    return IngestResult(status="queued", lead_id=lead.id)
