"""ai-sdr worker — runs the arq job loop in foreground."""

from __future__ import annotations

from arq.worker import run_worker


def worker() -> None:
    """Start the arq worker process. Blocks until SIGINT/SIGTERM."""
    from ai_sdr.worker.main import WorkerSettings

    run_worker(WorkerSettings)  # type: ignore[arg-type]
