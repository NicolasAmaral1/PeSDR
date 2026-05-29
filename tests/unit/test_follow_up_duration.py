"""parse_duration — ISO-8601 -> timedelta."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ai_sdr.follow_up.duration import parse_duration


def test_pt24h() -> None:
    assert parse_duration("PT24H") == timedelta(hours=24)


def test_pt2h30m() -> None:
    assert parse_duration("PT2H30M") == timedelta(hours=2, minutes=30)


def test_p1d() -> None:
    assert parse_duration("P1D") == timedelta(days=1)


def test_p7d() -> None:
    assert parse_duration("P7D") == timedelta(days=7)


def test_p1w() -> None:
    assert parse_duration("P1W") == timedelta(weeks=1)


def test_invalid_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_duration("24 hours")


def test_empty_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_duration("")


def test_pt0s_is_zero_delta() -> None:
    assert parse_duration("PT0S") == timedelta(0)
