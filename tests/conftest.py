"""Root pytest fixtures — shared by every test that needs DB + FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.main import create_app
from ai_sdr.settings import get_settings


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session against the running dev/test Postgres.

    Each test gets a fresh session that rolls back at the end so tests are
    isolated. Use `await db_session.commit()` inside the test only when you
    need cross-session visibility (e.g., the FastAPI app sees committed data)."""
    engine = create_async_engine(get_settings().database_url, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    """FastAPI app with lifespan executed (so arq_pool + adapter_registry
    are populated on app.state)."""
    a = create_app()
    async with a.router.lifespan_context(a):
        yield a
