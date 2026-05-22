"""Health endpoint: pings DB and Redis."""

from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session, redis_client

router = APIRouter()


@router.get("/health")
async def health(
    db: AsyncSession = Depends(db_session),
    rds: aioredis.Redis = Depends(redis_client),
) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"db unhealthy: {e}") from e

    try:
        pong = await rds.ping()
        redis_status = "ok" if pong else "fail"
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"redis unhealthy: {e}") from e

    return {"status": "ok", "db": db_status, "redis": redis_status}
