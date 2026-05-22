import json
import logging

import pytest
import structlog

from ai_sdr.logging_setup import configure_logging


def test_configure_logging_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO")
    log = structlog.get_logger()
    log.info("hello", tenant_id="abc")

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines, "no log line was emitted"
    parsed = json.loads(lines[-1])
    assert parsed["event"] == "hello"
    assert parsed["tenant_id"] == "abc"
    assert parsed["level"] == "info"
    assert "timestamp" in parsed


def test_configure_logging_respects_level() -> None:
    configure_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING
