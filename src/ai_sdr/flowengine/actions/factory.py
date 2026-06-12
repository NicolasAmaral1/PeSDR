"""Action adapter factory (FE-03c §7.3)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings

if TYPE_CHECKING:
    from ai_sdr.flowengine.actions.base import ActionAdapter
    from ai_sdr.schemas.tenant_yaml import TenantConfig


class UnknownAdapterError(Exception):
    """Raised when build_action_adapter is called for an unregistered name."""


def build_action_adapter(name: str, tenant: TenantConfig) -> ActionAdapter:
    if name not in ACTION_ADAPTERS:
        raise UnknownAdapterError(f"adapter {name!r} not registered")
    cls = ACTION_ADAPTERS[name]
    secrets_loader = SopsLoader(Path(get_settings().tenants_dir))
    # TenantConfig.id is the slug — validated by SLUG_RE in schemas/tenant_yaml.py.
    secrets = secrets_loader.load(tenant.id)
    return cls(tenant_config=tenant, secrets=secrets)
