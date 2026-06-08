"""Event model wraps the events row."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.event import Event
from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_event_round_trip(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    e = Event(
        tenant_id=tenant.id,
        event_type="turn.completed",
        payload={"talk_id": "x", "tokens_total_cost_usd": "0.02"},
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(e)
    await db_session.flush()

    fetched = (await db_session.execute(select(Event).where(Event.id == e.id))).scalar_one()
    assert fetched.event_type == "turn.completed"
    assert fetched.payload["tokens_total_cost_usd"] == "0.02"
    assert fetched.ingested_at is not None
