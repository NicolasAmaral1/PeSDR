"""ReengagementTemplate optional config under MessagingConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import MessagingConfig


def test_messaging_without_reengagement() -> None:
    cfg = MessagingConfig(provider="fake")
    assert cfg.reengagement_template is None


def test_reengagement_template_minimal() -> None:
    cfg = MessagingConfig.model_validate({
        "provider": "fake",
        "reengagement_template": {"template_ref": "reengagement_v1"},
    })
    assert cfg.reengagement_template is not None
    assert cfg.reengagement_template.template_ref == "reengagement_v1"
    assert cfg.reengagement_template.language == "pt_BR"
    assert cfg.reengagement_template.params == []


def test_reengagement_template_with_params() -> None:
    cfg = MessagingConfig.model_validate({
        "provider": "fake",
        "reengagement_template": {
            "template_ref": "reengagement_v1",
            "language": "pt_BR",
            "params": ["{{ collected.nome | default('amigo') }}"],
        },
    })
    assert cfg.reengagement_template is not None
    assert cfg.reengagement_template.params == ["{{ collected.nome | default('amigo') }}"]


def test_reengagement_template_ref_required() -> None:
    with pytest.raises(ValidationError):
        MessagingConfig.model_validate({
            "provider": "fake",
            "reengagement_template": {"language": "pt_BR"},
        })
