"""LoggingActionAdapter (fake) (FE-03c Task 9)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from ai_sdr.flowengine.actions.fake import LoggingActionAdapter
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS


def test_registered_under_logging_name():
    assert ACTION_ADAPTERS.get("logging") is LoggingActionAdapter


@pytest.mark.asyncio
async def test_execute_returns_deterministic_fake_id():
    tenant = SimpleNamespace(id="t1")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    r1 = await adapter.execute(handler="schedule_event", params={"a": 1})
    r2 = await adapter.execute(handler="schedule_event", params={"a": 1})
    assert r1.external_id == r2.external_id
    assert r1.external_id.startswith("fake-schedule_event-")


@pytest.mark.asyncio
async def test_execute_includes_params_in_detail():
    tenant = SimpleNamespace(id="t1")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    r = await adapter.execute(handler="x", params={"a": 1, "b": 2})
    assert r.detail == {"echo": {"a": 1, "b": 2}}


@pytest.mark.asyncio
async def test_execute_logs(caplog):
    tenant = SimpleNamespace(id="acme_test_slug")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    with caplog.at_level(logging.INFO):
        await adapter.execute(handler="schedule_event", params={"a": 1})
    assert any("acme_test_slug" in r.message for r in caplog.records)
