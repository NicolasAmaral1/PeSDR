"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
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
