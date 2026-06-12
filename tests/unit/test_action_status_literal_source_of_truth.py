"""ActionStatus Literal exports canonical enum values (FE-03c Task 1)."""
from __future__ import annotations

from ai_sdr.models.action_status import ALL_STATUSES, ActionStatus


def test_all_statuses_has_expected_length():
    assert len(ALL_STATUSES) == 4


def test_all_statuses_includes_pending():
    assert "pending" in ALL_STATUSES


def test_all_statuses_includes_executing():
    assert "executing" in ALL_STATUSES


def test_all_statuses_includes_success():
    assert "success" in ALL_STATUSES


def test_all_statuses_includes_failed():
    assert "failed" in ALL_STATUSES


def test_literal_matches_tuple():
    """ALL_STATUSES is the canonical tuple — derived from the Literal via get_args."""
    from typing import get_args

    assert ALL_STATUSES == get_args(ActionStatus)
