"""RLS test for the leads table — same pattern as kb_documents."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session, slug: str) -> Tenant:
    t = Tenant(slug=slug, display_name=slug.title())
    session.add(t)
    await session.flush()
    return t


async def test_lead_insert_and_select_under_tenant_context(db_session) -> None:
    tenant_a = await _make_tenant(db_session, f"a_{uuid.uuid4().hex[:6]}")
    tenant_b = await _make_tenant(db_session, f"b_{uuid.uuid4().hex[:6]}")
    await db_session.commit()

    # Insert lead under tenant A
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(Lead(tenant_id=tenant_a.id, whatsapp_e164="+5511999999991"))
    await db_session.commit()

    # Tenant A sees its lead
    await set_tenant_context(db_session, tenant_a.id)
    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert len(rows) == 1

    # Tenant B sees nothing
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert rows == []


async def test_lead_external_label_unique_per_tenant(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    db_session.add(Lead(tenant_id=tenant.id, external_label="test-1"))
    await db_session.commit()

    # Same label, same tenant → conflict
    db_session.add(Lead(tenant_id=tenant.id, external_label="test-1"))
    with pytest.raises(Exception):  # IntegrityError or wrapped
        await db_session.commit()
    await db_session.rollback()


async def test_lead_status_check_constraint(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    db_session.add(Lead(tenant_id=tenant.id, status="nonsense_status"))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()
