"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.simulate import simulate

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)


if __name__ == "__main__":  # pragma: no cover
    app()
