"""TurnDecision schema — treatment_status replaces treatment_resolved (FE-03a Task 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.decision import TurnDecision


def _base_kwargs(**overrides):
    return {
        "response_text": "ok",
        "collected_fields": {},
        "reasoning": "test",
        **overrides,
    }


def test_treatment_status_defaults_to_none():
    d = TurnDecision(**_base_kwargs())
    assert d.treatment_status is None


def test_treatment_status_accepts_in_progress():
    d = TurnDecision(**_base_kwargs(treatment_status="in_progress"))
    assert d.treatment_status == "in_progress"


def test_treatment_status_accepts_resolved_accepted():
    d = TurnDecision(**_base_kwargs(treatment_status="resolved_accepted"))
    assert d.treatment_status == "resolved_accepted"


def test_treatment_status_accepts_resolved_deferred():
    d = TurnDecision(**_base_kwargs(treatment_status="resolved_deferred"))
    assert d.treatment_status == "resolved_deferred"


def test_treatment_status_rejects_other_values():
    with pytest.raises(ValidationError):
        TurnDecision(**_base_kwargs(treatment_status="something_else"))


def test_treatment_resolved_field_removed():
    """The old boolean field is gone; using it raises."""
    with pytest.raises(ValidationError):
        TurnDecision(**_base_kwargs(treatment_resolved=True))
