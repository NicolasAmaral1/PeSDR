"""AdapterRegistry caches adapter instances per (tenant_id, provider)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def _stub_loader(provider: str = "fake"):
    """Returns (tenant_loader_mock, sops_loader_mock) wired with stubs."""
    tenant_cfg = MagicMock()
    tenant_cfg.messaging = MessagingConfig(provider=provider)
    tenant_loader = MagicMock()
    tenant_loader.load.return_value = tenant_cfg

    sops_loader = MagicMock()
    sops_loader.load.return_value = {}

    return tenant_loader, sops_loader


def _make_tenant(slug: str = "t") -> MagicMock:
    t = MagicMock()
    t.id = uuid.uuid4()
    t.slug = slug
    return t


def test_first_lookup_builds_adapter() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    assert isinstance(a, FakeMessagingAdapter)


def test_second_lookup_returns_cached_instance() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    b = registry.get(tenant, "fake")
    assert a is b
    assert tenant_loader.load.call_count == 1


def test_clear_resets_cache() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    registry.clear()
    b = registry.get(tenant, "fake")
    assert a is not b
