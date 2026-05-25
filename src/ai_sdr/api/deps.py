"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.session import get_session as _get_session
from ai_sdr.settings import get_settings


async def db_session() -> AsyncIterator[AsyncSession]:
    async for s in _get_session():
        yield s


async def redis_client() -> AsyncIterator[aioredis.Redis]:
    client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def arq_pool(request: Request) -> object:
    """Returns the per-process arq pool created at startup (see main.py)."""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise RuntimeError("arq_pool not initialized (lifespan didn't run)")
    return pool


def adapter_registry(request: Request) -> object:
    """Returns the per-process AdapterRegistry created at startup."""
    reg = getattr(request.app.state, "adapter_registry", None)
    if reg is None:
        raise RuntimeError("adapter_registry not initialized (lifespan didn't run)")
    return reg
