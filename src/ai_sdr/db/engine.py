"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_sdr.settings import get_settings


def create_engine() -> AsyncEngine:
    """Create the async engine using settings.database_url."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )


def build_engine(url: str) -> AsyncEngine:
    """Create an async engine from an explicit URL (used by the worker process)."""
    return create_async_engine(url, future=True, pool_pre_ping=True)
