"""Postgres checkpointer for LangGraph (uses psycopg3, not asyncpg).

Settings's database_url is a SQLAlchemy URL (`postgresql+asyncpg://...`);
this module rewrites it to a psycopg DSN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ai_sdr.settings import get_settings


def _to_psycopg_dsn(sqlalchemy_url: str) -> str:
    """Convert 'postgresql+asyncpg://...' to 'postgresql://...' for psycopg3."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def checkpointer_from_settings() -> AsyncIterator[AsyncPostgresSaver]:
    """Yield a connected AsyncPostgresSaver built from `settings.database_url`."""
    dsn = _to_psycopg_dsn(get_settings().database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        yield saver


async def ensure_checkpointer_schema() -> None:
    """Create the checkpointer's own tables (idempotent). Run once at startup."""
    async with checkpointer_from_settings() as saver:
        await saver.setup()
