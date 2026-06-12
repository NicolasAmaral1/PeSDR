"""worker.main registers execute_action in WorkerSettings.functions (FE-03c Task 13)."""

from __future__ import annotations

from ai_sdr.worker.jobs.execute_action import execute_action
from ai_sdr.worker.main import WorkerSettings


def test_execute_action_in_functions_list():
    assert execute_action in WorkerSettings.functions
