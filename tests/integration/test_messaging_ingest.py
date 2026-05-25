"""ingest helpers — find-or-create lead + dedupe inbound."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.ingest import (
    find_or_create_lead_by_address,
    ingest_inbound_message,
)
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session) -> Tenant:
    t = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    session.add(t)
    await session.flush()
    return t


async def test_find_or_create_creates_pending_lead(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    lead = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    await db_session.commit()
    assert lead.status == "pending_assignment"
    assert lead.whatsapp_e164 == "+5511999999999"


async def test_find_or_create_returns_existing_lead(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    first = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    await db_session.commit()

    # Tenant context is transaction-local; re-set after commit.
    await set_tenant_context(db_session, tenant.id)
    second = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    assert second.id == first.id


async def test_ingest_inbound_inserts_then_dedupes(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    msg = InboundMessage(
        external_id="wamid.ABC",
        from_address="+5511999999999",
        text="oi",
        received_at_iso=datetime.now(UTC).isoformat(),
        raw={"id": "wamid.ABC"},
    )

    r1 = await ingest_inbound_message(db_session, tenant, "whatsapp_cloud", msg)
    await db_session.commit()
    assert r1.status == "queued"

    # Tenant context is transaction-local; re-set after commit.
    await set_tenant_context(db_session, tenant.id)
    r2 = await ingest_inbound_message(db_session, tenant, "whatsapp_cloud", msg)
    await db_session.commit()
    assert r2.status == "skipped_dedupe"
    assert r2.lead_id == r1.lead_id

    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
