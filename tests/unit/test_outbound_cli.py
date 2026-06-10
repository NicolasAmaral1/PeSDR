"""ai-sdr outbound list — typer wiring + filter argument parsing."""

from __future__ import annotations

from typer.testing import CliRunner

from ai_sdr.cli.app import app

runner = CliRunner()


def test_outbound_list_help_includes_filters() -> None:
    r = runner.invoke(app, ["outbound", "list", "--help"])
    assert r.exit_code == 0
    assert "--tenant" in r.output
    assert "--lead" in r.output
    assert "--status" in r.output
    assert "--limit" in r.output


def test_outbound_list_requires_tenant() -> None:
    r = runner.invoke(app, ["outbound", "list"])
    assert r.exit_code != 0
    assert "tenant" in r.output.lower()
