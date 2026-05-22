"""Load + validate + cache TreeFlow YAML files from `tenants/<id>/treeflows/`."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_sdr.schemas.treeflow_yaml import TreeFlow


class TreeFlowNotFoundError(Exception):
    """Raised when a TreeFlow YAML file does not exist."""


class TreeFlowLoader:
    """Read TreeFlow YAML files per tenant. Cache by (tenant_id, treeflow_id)."""

    def __init__(self, tenants_dir: Path) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._cache: dict[tuple[str, str], TreeFlow] = {}

    def load(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        key = (tenant_id, treeflow_id)
        if key in self._cache:
            return self._cache[key]
        tf = self._read(tenant_id, treeflow_id)
        self._cache[key] = tf
        return tf

    def reload(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        tf = self._read(tenant_id, treeflow_id)
        self._cache[(tenant_id, treeflow_id)] = tf
        return tf

    def raw_yaml(self, tenant_id: str, treeflow_id: str) -> str:
        """Return the raw YAML text (used by runtime to snapshot a version)."""
        path = self._path(tenant_id, treeflow_id)
        if not path.is_file():
            raise TreeFlowNotFoundError(f"treeflow not found at {path}")
        return path.read_text(encoding="utf-8")

    def _path(self, tenant_id: str, treeflow_id: str) -> Path:
        return self._tenants_dir / tenant_id / "treeflows" / f"{treeflow_id}.yaml"

    def _read(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        path = self._path(tenant_id, treeflow_id)
        if not path.is_file():
            raise TreeFlowNotFoundError(f"treeflow not found at {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return TreeFlow.model_validate(data)
