"""RLS + dedupe test for inbound_messages."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session, slug: str) -> Tenant:
    t = Tenant(slug=slug, display_name=slug.title())
    session.add(t)
    await session.flush()
    return t


async def test_inbound_rls_isolation(db_session) -> None:
    tenant_a = await _make_tenant(db_session, f"a_{uuid.uuid4().hex[:6]}")
    tenant_b = await _make_tenant(db_session, f"b_{uuid.uuid4().hex[:6]}")
    await db_session.commit()

    await set_tenant_context(db_session, tenant_a.id)
    lead_a = Lead(tenant_id=tenant_a.id, whatsapp_e164="+5511999999991", status="active")
    db_session.add(lead_a)
    await db_session.flush()
    db_session.add(
        InboundMessageRow(
            tenant_id=tenant_a.id,
            provider="whatsapp_cloud",
            external_id="wa_msg_1",
            lead_id=lead_a.id,
            from_address="+5511999999991",
            text="oi",
            received_at=datetime.now(UTC),
            raw={"id": "wa_msg_1"},
        )
    )
    await db_session.commit()

    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert rows == []


async def test_inbound_dedupe_via_on_conflict(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status="active")
    db_session.add(lead)
    await db_session.flush()

    values = {
        "tenant_id": tenant.id,
        "provider": "whatsapp_cloud",
        "external_id": "dup_1",
        "lead_id": lead.id,
        "from_address": "+5511999999999",
        "text": "first",
        "received_at": datetime.now(UTC),
        "raw": {"id": "dup_1"},
    }
    r1 = await db_session.execute(
        pg_insert(InboundMessageRow).values(**values).on_conflict_do_nothing()
    )
    r2 = await db_session.execute(
        pg_insert(InboundMessageRow).values(**{**values, "text": "second"}).on_conflict_do_nothing()
    )
    await db_session.commit()
    assert r1.rowcount == 1
    assert r2.rowcount == 0

    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "first"  # second was rejected silently
