"""ActionExecutionRepository surface (FE-03c Task 10)."""

from __future__ import annotations

import inspect

from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository


def test_class_exists():
    assert ActionExecutionRepository is not None


def test_insert_pending_is_async():
    sig = inspect.signature(ActionExecutionRepository.insert_pending)
    assert inspect.iscoroutinefunction(ActionExecutionRepository.insert_pending)
    params = list(sig.parameters)
    assert params == [
        "self",
        "tenant_id",
        "talk_id",
        "node_id",
        "field",
        "value_hash",
        "adapter_name",
        "handler",
        "params_resolved",
    ]


def test_mark_executing_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_executing)


def test_mark_success_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_success)


def test_mark_failed_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_failed)
