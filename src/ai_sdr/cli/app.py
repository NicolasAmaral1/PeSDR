"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.reindex_kb import reindex_kb_app
from ai_sdr.cli.simulate import simulate

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)
app.add_typer(reindex_kb_app, name="reindex-kb")


if __name__ == "__main__":  # pragma: no cover
    app()
