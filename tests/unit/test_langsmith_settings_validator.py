"""validate_langsmith_config — warn when tracing enabled without API key."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_sdr.logging_setup import configure_logging
from ai_sdr.main import _validate_langsmith_config


def _settings(*, tracing=False, api_key=None, project="pesdr-dev"):
    s = MagicMock()
    s.langchain_tracing_v2 = tracing
    s.langsmith_api_key = api_key
    s.langchain_project = project
    return s


def test_passes_when_tracing_disabled() -> None:
    _validate_langsmith_config(_settings(tracing=False))  # no raise, no warn


def test_passes_when_tracing_enabled_with_api_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="WARNING")
    _validate_langsmith_config(_settings(tracing=True, api_key="ls__abc"))
    out = capsys.readouterr().out
    assert "LANGSMITH_API_KEY" not in out


def test_warns_when_tracing_enabled_without_api_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="WARNING")
    _validate_langsmith_config(_settings(tracing=True, api_key=None))
    out = capsys.readouterr().out
    assert "LANGSMITH_API_KEY" in out
    assert "silently" in out.lower() or "no-op" in out.lower()
