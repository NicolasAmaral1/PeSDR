"""LeadRepository — lookup + identity field updates."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.repositories.lead_repository import LeadRepository


@pytest.mark.asyncio
async def test_find_by_channel_identifier(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    lead = Lead(
        tenant_id=tenant.id,
        channel_identifiers={"whatsapp": "+5511999999999"},
    )
    db_session.add(lead)
    await db_session.flush()

    repo = LeadRepository(db_session)
    found = await repo.find_by_channel_identifier(
        tenant.id, "whatsapp", "+5511999999999"
    )
    assert found is not None
    assert found.id == lead.id


@pytest.mark.asyncio
async def test_find_by_channel_identifier_returns_none_when_missing(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    repo = LeadRepository(db_session)
    assert (
        await repo.find_by_channel_identifier(tenant.id, "whatsapp", "+nope")
        is None
    )


@pytest.mark.asyncio
async def test_set_risk_level_updates_audit_columns(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    await db_session.flush()

    repo = LeadRepository(db_session)
    await repo.set_risk_level(lead, "elevated", reason="spamming")
    await db_session.flush()
    assert lead.risk_level == "elevated"
    assert lead.risk_level_reason == "spamming"
    assert lead.risk_level_since is not None
