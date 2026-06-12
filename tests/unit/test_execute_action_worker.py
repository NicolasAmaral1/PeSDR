"""execute_action worker job (FE-03c Task 12).

Test surface: success path, terminal failure (attempts >= 3), retry path
(attempts < 3 → raise so arq re-enqueues), execution-not-found early return.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.worker.jobs.execute_action import execute_action


@asynccontextmanager
async def _fake_session_factory_cm(session_mock):
    yield session_mock


@pytest.fixture
def fake_ctx():
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()

    def factory():
        return _fake_session_factory_cm(session_mock)

    return {
        "session_factory": factory,
        "_session": session_mock,
    }


@pytest.mark.asyncio
async def test_execution_not_found_returns_early(fake_ctx):
    execution_id = uuid4()
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=None)

    with patch(
        "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
        return_value=repo_mock,
    ):
        await execute_action(fake_ctx, str(execution_id))

    repo_mock.mark_executing.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_path_marks_success_and_commits(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id,
        tenant_id=uuid4(),
        adapter_name="logging",
        handler="schedule_event",
        params_resolved={"a": 1},
        attempts=1,
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_success = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(return_value=ActionResult(external_id="ext-1"))

    with (
        patch(
            "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
            return_value=repo_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.build_action_adapter",
            return_value=adapter_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
            AsyncMock(return_value=SimpleNamespace(slug="t1")),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.set_tenant_context",
            AsyncMock(),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._refetch_locked",
            AsyncMock(return_value=fake_execution),
        ),
    ):
        await execute_action(fake_ctx, str(execution_id))

    adapter_mock.execute.assert_awaited_once_with(handler="schedule_event", params={"a": 1})
    repo_mock.mark_success.assert_awaited_once()
    assert repo_mock.mark_success.await_args.kwargs["external_id"] == "ext-1"


@pytest.mark.asyncio
async def test_retry_path_raises_so_arq_reenqueues(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id,
        tenant_id=uuid4(),
        adapter_name="logging",
        handler="x",
        params_resolved={},
        attempts=1,
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_failed = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
            return_value=repo_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.build_action_adapter",
            return_value=adapter_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
            AsyncMock(return_value=SimpleNamespace(slug="t1")),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.set_tenant_context",
            AsyncMock(),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._refetch_locked",
            AsyncMock(return_value=fake_execution),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await execute_action(fake_ctx, str(execution_id))

    repo_mock.mark_failed.assert_awaited_once()
    assert repo_mock.mark_failed.await_args.kwargs["terminal"] is False


@pytest.mark.asyncio
async def test_terminal_failure_after_3_attempts(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id,
        tenant_id=uuid4(),
        adapter_name="logging",
        handler="x",
        params_resolved={},
        attempts=3,
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_failed = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
            return_value=repo_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.build_action_adapter",
            return_value=adapter_mock,
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
            AsyncMock(return_value=SimpleNamespace(slug="t1")),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action.set_tenant_context",
            AsyncMock(),
        ),
        patch(
            "ai_sdr.worker.jobs.execute_action._refetch_locked",
            AsyncMock(return_value=fake_execution),
        ),
    ):
        await execute_action(fake_ctx, str(execution_id))

    repo_mock.mark_failed.assert_awaited_once()
    assert repo_mock.mark_failed.await_args.kwargs["terminal"] is True
