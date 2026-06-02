"""Verifies migration 0017 creates response_reviews table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_response_reviews_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'response_reviews' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "talk_id", "turn_index",
        "correction_iteration", "parent_review_id",
        "original_response", "original_turn_decision", "original_system_prompt_snapshot",
        "status", "operator_id", "decision_at",
        "edited_response", "edit_reason",
        "rejection_reason", "improvement_category",
        "final_response_sent", "created_at", "expires_at",
    }
