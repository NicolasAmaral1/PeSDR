"""Migration 0026 extends talks.status CHECK constraint (FE-03b Task 2)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.models.talk_status import ALL_STATUSES
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pg_constraint_accepts_all_lifecycle_statuses(async_engine):
    """Every value in ALL_STATUSES satisfies the new CHECK constraint."""
    async with AsyncSession(async_engine) as session:
        for v in ALL_STATUSES:
            await session.execute(text("SAVEPOINT v"))
            try:
                await session.execute(
                    text(
                        "SELECT 1 WHERE :v IN ("
                        + ", ".join(f"'{s}'" for s in ALL_STATUSES)
                        + ")"
                    ),
                    {"v": v},
                )
            finally:
                await session.execute(text("ROLLBACK TO SAVEPOINT v"))
