"""worker.main registers scheduled_scan_talks (FE-03b Task 16)."""

from __future__ import annotations


def test_scheduled_scan_talks_in_cron_jobs():
    from ai_sdr.worker import main

    # cron_jobs is the arq list configured at module level. arq.cron.CronJob
    # stores the registered callable as `coroutine`; raw callables (legacy
    # form) expose `__name__` directly.
    names = [getattr(job, "coroutine", job).__name__ for job in main.cron_jobs]
    assert "scheduled_scan_talks" in names


def test_scheduled_scan_talks_function_exists():
    from ai_sdr.worker.main import scheduled_scan_talks

    assert callable(scheduled_scan_talks)
