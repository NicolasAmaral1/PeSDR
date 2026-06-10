"""Talk model exposes requires_review_reason column (FE-03a Task 4)."""

from __future__ import annotations

from ai_sdr.models.talk import Talk


def test_talk_has_requires_review_reason_attribute():
    assert hasattr(Talk, "requires_review_reason")


def test_requires_review_reason_default_none():
    t = Talk.__new__(Talk)
    assert getattr(t, "requires_review_reason", None) is None
