"""Talk.status ORM column references TalkStatus Literal (FE-03b Task 3)."""

from __future__ import annotations

from typing import get_args

from ai_sdr.models.talk import Talk
from ai_sdr.models.talk_status import ALL_STATUSES, TalkStatus


def test_status_column_exists():
    assert hasattr(Talk, "status")


def test_status_column_is_text_type():
    col = Talk.__table__.c.status
    type_str = str(col.type).upper()
    assert "TEXT" in type_str or "VARCHAR" in type_str


def test_status_column_not_nullable():
    assert Talk.__table__.c.status.nullable is False


def test_all_known_statuses_can_be_assigned_at_typing_level():
    """Type narrowing check — every value in ALL_STATUSES is a valid TalkStatus."""
    assert set(get_args(TalkStatus)) == set(ALL_STATUSES)
