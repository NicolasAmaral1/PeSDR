"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.follow_ups import follow_ups_app
from ai_sdr.cli.leads import leads_app
from ai_sdr.cli.outbound import outbound_app
from ai_sdr.cli.reindex_kb import reindex_kb_app
from ai_sdr.cli.simulate import simulate
from ai_sdr.cli.worker import worker

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)
app.add_typer(reindex_kb_app, name="reindex-kb")
app.add_typer(leads_app, name="leads")
app.add_typer(follow_ups_app, name="follow-ups")
app.add_typer(outbound_app, name="outbound")
app.command(name="worker")(worker)


if __name__ == "__main__":  # pragma: no cover
    app()
