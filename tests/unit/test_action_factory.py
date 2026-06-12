"""Action factory: build_action_adapter + UnknownAdapterError (FE-03c Task 7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.factory import (
    UnknownAdapterError,
    build_action_adapter,
)
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS


@pytest.fixture(autouse=True)
def reset_registry():
    snapshot = dict(ACTION_ADAPTERS)
    yield
    ACTION_ADAPTERS.clear()
    ACTION_ADAPTERS.update(snapshot)


def _stub_tenant(slug="example"):
    # TenantConfig.id IS the slug.
    return SimpleNamespace(id=slug, slug=slug)


def test_build_instantiates_registered_adapter():
    constructed_with = {}

    class FactoryTestAdapter(ActionAdapter):
        name = "factory_test"

        def __init__(self, *, tenant_config, secrets):
            super().__init__(tenant_config=tenant_config, secrets=secrets)
            constructed_with["tenant"] = tenant_config
            constructed_with["secrets"] = secrets

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    ACTION_ADAPTERS["factory_test"] = FactoryTestAdapter

    fake_secrets = {"some_key": "some_value"}
    with patch("ai_sdr.flowengine.actions.factory.SopsLoader") as MockSops:
        loader_instance = MagicMock()
        loader_instance.load.return_value = fake_secrets
        MockSops.return_value = loader_instance

        adapter = build_action_adapter("factory_test", _stub_tenant("acme"))

    assert isinstance(adapter, FactoryTestAdapter)
    assert constructed_with["tenant"].id == "acme"
    assert constructed_with["secrets"] == fake_secrets


def test_unknown_adapter_raises():
    with pytest.raises(UnknownAdapterError, match="not registered"):
        build_action_adapter("ghost_adapter_xyz", _stub_tenant())
