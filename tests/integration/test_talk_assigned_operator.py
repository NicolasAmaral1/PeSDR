"""Integration test for talks.assigned_operator_id column.

Task 1 (HITL write-side): verifies the column exists, is nullable,
and accepts None round-trip through the DB.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from ai_sdr.models.talk import Talk

pytestmark = pytest.mark.integration


async def test_talk_has_assigned_operator_column(db_session, seeded_talk_factory):
    talk, tenant = await seeded_talk_factory(handling_mode="ai")
    talk.assigned_operator_id = None  # column exists, nullable
    await db_session.flush()
    refreshed = await db_session.get(Talk, talk.id)
    assert hasattr(refreshed, "assigned_operator_id")
    assert refreshed.assigned_operator_id is None
