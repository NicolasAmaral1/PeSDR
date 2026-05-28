"""validate_langsmith_config — warn when tracing enabled without API key."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from ai_sdr.main import _validate_langsmith_config


def _settings(*, tracing=False, api_key=None, project="pesdr-dev"):
    s = MagicMock()
    s.langchain_tracing_v2 = tracing
    s.langsmith_api_key = api_key
    s.langchain_project = project
    return s


def test_passes_when_tracing_disabled() -> None:
    _validate_langsmith_config(_settings(tracing=False))  # no raise, no warn


def test_passes_when_tracing_enabled_with_api_key(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _validate_langsmith_config(_settings(tracing=True, api_key="ls__abc"))
    assert "LANGSMITH_API_KEY" not in caplog.text


def test_warns_when_tracing_enabled_without_api_key(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _validate_langsmith_config(_settings(tracing=True, api_key=None))
    assert "LANGSMITH_API_KEY" in caplog.text
    assert "silently" in caplog.text.lower() or "no-op" in caplog.text.lower()
