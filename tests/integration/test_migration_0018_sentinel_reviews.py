"""Verifies migration 0018 creates sentinel_reviews table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_sentinel_reviews_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sentinel_reviews' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id",
        "tenant_id",
        "lead_id",
        "talk_id",
        "inbound_message_id",
        "triggered_by",
        "classification",
        "reasoning",
        "confidence",
        "risk_level_before",
        "risk_level_after",
        "heuristic_matches",
        "created_at",
    }
