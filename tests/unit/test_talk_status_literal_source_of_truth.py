"""TalkStatus Literal exports canonical enum values (FE-03b Task 1)."""

from __future__ import annotations

from ai_sdr.models.talk_status import ALL_STATUSES, TalkStatus


def test_all_statuses_has_expected_length():
    assert len(ALL_STATUSES) == 10


def test_all_statuses_contains_pre_fe03b_values():
    """Backward-compat: pre-FE-03b statuses still present."""
    for v in (
        "active",
        "requires_review",
        "closed_completed",
        "closed_inactivity",
        "closed_optout",
        "closed_banned",
    ):
        assert v in ALL_STATUSES


def test_all_statuses_contains_fe03b_new_values():
    """New FE-03b values present."""
    for v in (
        "closed_completed_success",
        "closed_completed_failure",
        "closed_no_interest",
        "closed_duration",
    ):
        assert v in ALL_STATUSES


def test_talk_status_is_literal():
    """TalkStatus is a Literal type covering ALL_STATUSES exactly."""
    from typing import get_args

    assert set(get_args(TalkStatus)) == set(ALL_STATUSES)
