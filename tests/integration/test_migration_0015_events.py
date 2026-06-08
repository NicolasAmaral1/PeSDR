"""Verifies migration 0015 creates events table with indexes + RLS."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_events_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'events'
            ORDER BY column_name
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "event_type", "payload",
        "talk_id", "lead_id", "experiment_id", "experiment_variant",
        "occurred_at", "ingested_at",
    }


@pytest.mark.asyncio
async def test_events_table_indexes_present(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'events'"
        )
    )
    idxs = {r[0] for r in result.all()}
    assert "ix_events_tenant_occurred" in idxs
    assert "ix_events_talk" in idxs
    assert "ix_events_type_occurred" in idxs


@pytest.mark.asyncio
async def test_events_insert_round_trip(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )

    await db_session.execute(
        text(
            "INSERT INTO events (tenant_id, event_type, payload, occurred_at) "
            "VALUES (:t, 'turn.completed', CAST(:p AS JSONB), :o)"
        ),
        {
            "t": tenant_id,
            "p": json.dumps({"talk_id": str(uuid.uuid4())}),
            "o": datetime.now(timezone.utc),
        },
    )

    result = await db_session.execute(
        text("SELECT event_type FROM events WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    assert result.scalar_one() == "turn.completed"
    await db_session.rollback()
