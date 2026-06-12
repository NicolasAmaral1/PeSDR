"""Action registry: @register decorator + ACTION_ADAPTERS dict (FE-03c Task 6)."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS, register


@pytest.fixture(autouse=True)
def reset_registry():
    """Snapshot/restore ACTION_ADAPTERS to keep tests independent."""
    snapshot = dict(ACTION_ADAPTERS)
    yield
    ACTION_ADAPTERS.clear()
    ACTION_ADAPTERS.update(snapshot)


def test_register_adds_to_dict():
    @register
    class A(ActionAdapter):
        name = "test_adapter_a"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    assert ACTION_ADAPTERS["test_adapter_a"] is A


def test_register_returns_class_unchanged():
    class B(ActionAdapter):
        name = "test_adapter_b"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    decorated = register(B)
    assert decorated is B


def test_register_rejects_missing_name():
    class NoName(ActionAdapter):
        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    with pytest.raises(ValueError, match="missing `name`"):
        register(NoName)


def test_register_rejects_duplicate_name():
    @register
    class C(ActionAdapter):
        name = "test_adapter_dup"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    class CAgain(ActionAdapter):
        name = "test_adapter_dup"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    with pytest.raises(ValueError, match="already registered"):
        register(CAgain)
