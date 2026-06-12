"""arq enqueue helpers (FE-03c)."""

from __future__ import annotations

from arq.connections import ArqRedis, RedisSettings, create_pool

from ai_sdr.settings import get_settings

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue_execute_action(execution_id_str: str) -> None:
    pool = await _get_pool()
    await pool.enqueue_job("execute_action", execution_id_str)
