"""process_lead_inbox routes by tenant.architecture_version."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.tenant import Tenant
from ai_sdr.worker.jobs.inbound import process_lead_inbox_for_test_routing


@pytest.mark.asyncio
async def test_v1_tenant_routes_to_legacy(db_session: AsyncSession) -> None:
    tenant = Tenant(
        slug=f"v1-{uuid.uuid4().hex[:8]}",
        display_name="v1",
        architecture_version=1,
    )
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()

    with (
        patch("ai_sdr.worker.jobs.inbound._run_legacy_pipeline", new_callable=AsyncMock) as legacy,
        patch("ai_sdr.worker.jobs.inbound._run_v2_pipeline", new_callable=AsyncMock) as v2,
    ):
        await process_lead_inbox_for_test_routing(db_session, tenant=tenant, inbound=inbound)
    legacy.assert_awaited_once()
    v2.assert_not_called()


@pytest.mark.asyncio
async def test_v2_tenant_routes_to_flowengine(db_session: AsyncSession) -> None:
    tenant = Tenant(
        slug=f"v2-{uuid.uuid4().hex[:8]}",
        display_name="v2",
        architecture_version=2,
    )
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="fake",
        external_id=f"ext-{uuid.uuid4().hex[:6]}",
        from_address="+5511999999999",
        text="oi",
        raw={"body": "oi"},
        media_type="text",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound)
    await db_session.flush()

    with (
        patch("ai_sdr.worker.jobs.inbound._run_legacy_pipeline", new_callable=AsyncMock) as legacy,
        patch("ai_sdr.worker.jobs.inbound._run_v2_pipeline", new_callable=AsyncMock) as v2,
    ):
        await process_lead_inbox_for_test_routing(db_session, tenant=tenant, inbound=inbound)
    v2.assert_awaited_once()
    legacy.assert_not_called()
