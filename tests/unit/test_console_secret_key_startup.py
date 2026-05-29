"""Startup-time validation of CONSOLE_SECRET_KEY against console.enabled tenants."""

from __future__ import annotations

import pytest

from ai_sdr.main import _validate_console_secret_key_if_needed


class _FakeSettings:
    def __init__(self, tenants_dir, secret):
        self.tenants_dir = tenants_dir
        self.console_secret_key = secret


def test_passes_when_no_tenants_dir(tmp_path) -> None:
    _validate_console_secret_key_if_needed(_FakeSettings(tmp_path / "nope", None))


def test_passes_when_no_tenant_has_console_enabled(tmp_path) -> None:
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "tenant.yaml").write_text(
        "id: t1\ndisplay_name: T1\ntimezone: UTC\n"
        "llm:\n  default:\n    provider: anthropic\n    model: claude-sonnet-4-6\n"
        "    api_key_ref: secrets/anthropic_key\n"
    )
    _validate_console_secret_key_if_needed(_FakeSettings(tmp_path, None))


def test_raises_when_tenant_enabled_but_secret_missing(tmp_path) -> None:
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "tenant.yaml").write_text(
        "id: t1\ndisplay_name: T1\ntimezone: UTC\n"
        "llm:\n  default:\n    provider: anthropic\n    model: claude-sonnet-4-6\n"
        "    api_key_ref: secrets/anthropic_key\n"
        "console:\n  enabled: true\n"
    )
    with pytest.raises(RuntimeError, match="CONSOLE_SECRET_KEY is unset"):
        _validate_console_secret_key_if_needed(_FakeSettings(tmp_path, None))


def test_raises_when_secret_too_short(tmp_path) -> None:
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "tenant.yaml").write_text(
        "id: t1\ndisplay_name: T1\ntimezone: UTC\n"
        "llm:\n  default:\n    provider: anthropic\n    model: claude-sonnet-4-6\n"
        "    api_key_ref: secrets/anthropic_key\n"
        "console:\n  enabled: true\n"
    )
    with pytest.raises(RuntimeError, match="32\\+ chars"):
        _validate_console_secret_key_if_needed(_FakeSettings(tmp_path, "short"))


def test_passes_when_secret_valid(tmp_path) -> None:
    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "tenant.yaml").write_text(
        "id: t1\ndisplay_name: T1\ntimezone: UTC\n"
        "llm:\n  default:\n    provider: anthropic\n    model: claude-sonnet-4-6\n"
        "    api_key_ref: secrets/anthropic_key\n"
        "console:\n  enabled: true\n"
    )
    _validate_console_secret_key_if_needed(_FakeSettings(tmp_path, "x" * 48))
