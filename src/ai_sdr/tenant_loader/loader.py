"""Load and validate tenant YAML config files."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_sdr.schemas.tenant_yaml import TenantConfig


class TenantNotFoundError(Exception):
    """Raised when a tenant directory does not exist."""


class TenantLoader:
    """Load tenant.yaml files from disk, validate, and cache."""

    def __init__(self, tenants_dir: Path) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._cache: dict[str, TenantConfig] = {}

    def load(self, tenant_id: str) -> TenantConfig:
        """Return cached config or read from disk."""
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        cfg = self._read(tenant_id)
        self._cache[tenant_id] = cfg
        return cfg

    def reload(self, tenant_id: str) -> TenantConfig:
        """Force re-read from disk, bypassing cache."""
        cfg = self._read(tenant_id)
        self._cache[tenant_id] = cfg
        return cfg

    def _read(self, tenant_id: str) -> TenantConfig:
        path = self._tenants_dir / tenant_id / "tenant.yaml"
        if not path.is_file():
            raise TenantNotFoundError(f"tenant config not found at {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return TenantConfig.model_validate(data)
