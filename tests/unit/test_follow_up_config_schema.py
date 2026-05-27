"""FollowUpConfig schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, FollowUpStep


def test_disabled_default() -> None:
    cfg = FollowUpConfig()
    assert cfg.enabled is False
    assert cfg.max_attempts == 3
    assert cfg.sequence == []


def test_enabled_with_full_sequence() -> None:
    cfg = FollowUpConfig.model_validate({
        "enabled": True,
        "max_attempts": 2,
        "sequence": [
            {"after": "PT24H", "template_ref": "followup_24h_v1"},
            {"after": "P3D", "template_ref": "followup_72h_v1"},
        ],
    })
    assert cfg.enabled is True
    assert len(cfg.sequence) == 2
    assert cfg.sequence[0].language == "pt_BR"  # default
    assert cfg.sequence[0].params == []


def test_enabled_requires_sequence_at_least_max_attempts() -> None:
    with pytest.raises(ValidationError, match="sequence has 1 entries"):
        FollowUpConfig.model_validate({
            "enabled": True,
            "max_attempts": 3,
            "sequence": [
                {"after": "PT24H", "template_ref": "x"},
            ],
        })


def test_disabled_allows_empty_sequence() -> None:
    cfg = FollowUpConfig.model_validate({
        "enabled": False,
        "max_attempts": 3,
        "sequence": [],
    })
    assert cfg.enabled is False


def test_max_attempts_bounds() -> None:
    with pytest.raises(ValidationError):
        FollowUpConfig.model_validate({"max_attempts": 0})
    with pytest.raises(ValidationError):
        FollowUpConfig.model_validate({"max_attempts": 11})


def test_after_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError, match="invalid ISO-8601 duration"):
        FollowUpStep.model_validate({"after": "24 hours", "template_ref": "x"})


def test_after_accepts_iso_8601_variants() -> None:
    for d in ("PT24H", "PT2H30M", "P1D", "P7D", "P1W"):
        s = FollowUpStep.model_validate({"after": d, "template_ref": "t"})
        assert s.after == d


def test_params_default_empty_list() -> None:
    s = FollowUpStep.model_validate({"after": "PT1H", "template_ref": "t"})
    assert s.params == []
