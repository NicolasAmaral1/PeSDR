"""ActionAdapter ABC + ActionResult dataclass (FE-03c Task 5)."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult


def test_action_result_with_external_id():
    r = ActionResult(external_id="evt_123")
    assert r.external_id == "evt_123"
    assert r.detail is None


def test_action_result_with_detail():
    r = ActionResult(external_id="evt_123", detail={"echo": "ok"})
    assert r.detail == {"echo": "ok"}


def test_action_result_external_id_can_be_none():
    r = ActionResult(external_id=None)
    assert r.external_id is None


def test_cannot_instantiate_abstract_adapter():
    with pytest.raises(TypeError):
        ActionAdapter(tenant_config=None, secrets={})  # type: ignore[abstract]


def test_concrete_subclass_requires_name():
    """Subclasses without `name` attribute can be defined but registering fails (covered in T6)."""

    class Concrete(ActionAdapter):
        name = "concrete_test"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    inst = Concrete(tenant_config=None, secrets={})  # type: ignore[arg-type]
    assert inst.name == "concrete_test"
