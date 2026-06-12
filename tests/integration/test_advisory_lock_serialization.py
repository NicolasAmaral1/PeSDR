"""advisory_lock.acquire serializes concurrent acquisitions per (tenant, lead)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.db.advisory_lock import acquire_lead_lock
from ai_sdr.settings import get_settings


@pytest.mark.asyncio
async def test_two_concurrent_acquisitions_serialize() -> None:
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    order: list[str] = []

    async def hold_then_release(label: str, hold_ms: int) -> None:
        async with sessionmaker() as session:
            async with session.begin():
                await acquire_lead_lock(session, tenant_id, lead_id)
                order.append(f"enter:{label}")
                await asyncio.sleep(hold_ms / 1000)
                order.append(f"exit:{label}")

    await asyncio.gather(
        hold_then_release("a", 200),
        hold_then_release("b", 50),
    )
    await engine.dispose()

    # 'a' acquired first; 'b' must wait until 'a' released.
    assert order == ["enter:a", "exit:a", "enter:b", "exit:b"]


@pytest.mark.asyncio
async def test_different_leads_do_not_serialize() -> None:
    """Different (tenant, lead) pairs acquire independently — no contention."""
    tenant_id = uuid.uuid4()
    lead_a, lead_b = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    order: list[str] = []

    async def hold(label: str, lead_id: uuid.UUID, hold_ms: int) -> None:
        async with sessionmaker() as session:
            async with session.begin():
                await acquire_lead_lock(session, tenant_id, lead_id)
                order.append(f"enter:{label}")
                await asyncio.sleep(hold_ms / 1000)
                order.append(f"exit:{label}")

    await asyncio.gather(
        hold("a", lead_a, 100),
        hold("b", lead_b, 100),
    )
    await engine.dispose()

    # Both should be in flight together: a enters, b enters, then both exit.
    assert order[:2] == ["enter:a", "enter:b"] or order[:2] == ["enter:b", "enter:a"]
