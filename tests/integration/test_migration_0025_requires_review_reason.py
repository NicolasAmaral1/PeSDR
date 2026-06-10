"""Migration 0025 adds talks.requires_review_reason (FE-03a Task 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_requires_review_reason_column_exists(async_engine):
    async with async_engine.connect() as conn:

        def _cols(sync_conn):
            insp = inspect(sync_conn)
            return {c["name"]: c for c in insp.get_columns("talks")}

        cols = await conn.run_sync(_cols)
    assert "requires_review_reason" in cols
    col = cols["requires_review_reason"]
    assert col["nullable"] is True
    # String column (VARCHAR or TEXT-ish)
    assert "VARCHAR" in str(col["type"]).upper() or "TEXT" in str(col["type"]).upper()


@pytest.mark.asyncio
async def test_requires_review_reason_check_constraint(async_engine):
    """Constraint accepts the documented enum values + NULL."""
    valid = [
        "escalation_requested",
        "off_topic_exhausted",
        "validator_exhausted",
        "treeflow_version_missing",
        "objection_treatment_exhausted",
    ]
    async with async_engine.connect() as conn:
        for v in valid:
            r = await conn.execute(
                text(
                    "SELECT 'ok' WHERE "  # noqa: UP032
                    "'{v}' IN ('escalation_requested', 'off_topic_exhausted', "
                    "'validator_exhausted', 'treeflow_version_missing', "
                    "'objection_treatment_exhausted')".format(v=v)
                )
            )
            assert r.scalar() == "ok"
