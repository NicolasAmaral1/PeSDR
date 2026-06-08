"""Verifies migration 0020 creates treeflow_improvement_suggestions table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_treeflow_improvement_suggestions_columns(
    db_session: AsyncSession,
) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'treeflow_improvement_suggestions' "
            "ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "treeflow_id", "target_node_id",
        "pattern_summary", "sample_count", "sample_review_ids",
        "suggested_change", "suggested_change_natural_language",
        "confidence", "status", "operator_decision_at", "created_at",
    }
