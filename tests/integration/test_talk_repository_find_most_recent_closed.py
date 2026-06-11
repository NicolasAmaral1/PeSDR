"""TalkRepository.find_most_recent_closed (FE-03b Task 13)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_returns_none_when_no_closed_talk_exists(
    db_session, tenant_factory, lead_factory, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(db_session)
    tenant = await tenant_factory()
    lead = await lead_factory(tenant_id=tenant.id)
    await talk_factory(tenant_id=tenant.id, lead_id=lead.id, status="active")
    closed = await repo.find_most_recent_closed(tenant.id, lead.id)
    assert closed is None


@pytest.mark.asyncio
async def test_returns_most_recent_closed_talk(
    db_session, tenant_factory, lead_factory, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(db_session)
    tenant = await tenant_factory()
    lead = await lead_factory(tenant_id=tenant.id)
    old = await talk_factory(
        tenant_id=tenant.id, lead_id=lead.id, status="closed_inactivity",
    )
    new = await talk_factory(
        tenant_id=tenant.id, lead_id=lead.id, status="closed_completed_success",
    )
    closed = await repo.find_most_recent_closed(tenant.id, lead.id)
    assert closed is not None
    assert closed.id == new.id


@pytest.mark.asyncio
async def test_ignores_active_talks(
    db_session, tenant_factory, lead_factory, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(db_session)
    tenant = await tenant_factory()
    lead = await lead_factory(tenant_id=tenant.id)
    await talk_factory(tenant_id=tenant.id, lead_id=lead.id, status="active")
    closed = await repo.find_most_recent_closed(tenant.id, lead.id)
    assert closed is None
