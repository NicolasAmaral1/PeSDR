"""Root pytest fixtures — shared by every test that needs DB + FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.main import create_app
from ai_sdr.settings import get_settings


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session against the running dev/test Postgres.

    Each test gets a fresh session that rolls back at the end so tests are
    isolated. Use `await db_session.commit()` inside the test only when you
    need cross-session visibility (e.g., the FastAPI app sees committed data)."""
    # NullPool avoids dangling asyncpg connections that race per-test event
    # loop teardown ("Event loop is closed" during engine.dispose()).
    engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
def app() -> FastAPI:
    """FastAPI app — tests populate app.state directly (no lifespan).

    The lifespan creates a real arq Redis pool whose teardown races the
    per-test asyncio event loop teardown. Tests that hit endpoints with
    arq_pool / adapter_registry deps must set them on app.state via their
    own fixtures (see the `signed_app` pattern in test_webhook_routes).
    """
    return create_app()
