"""Decrypt SOPS-encrypted YAML files using the `sops` CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml


class SopsDecryptError(Exception):
    """Raised when sops fails to decrypt."""


class SopsLoader:
    """Decrypt and cache tenant secrets files."""

    def __init__(self, tenants_dir: Path, project_root: Path | None = None) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._project_root = Path(project_root) if project_root else Path.cwd()
        self._cache: dict[str, dict[str, Any]] = {}

    def load(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id in self._cache:
            return self._cache[tenant_id]
        path = self._tenants_dir / tenant_id / "secrets.enc.yaml"
        if not path.is_file():
            raise SopsDecryptError(f"secrets file not found at {path}")
        try:
            result = subprocess.run(  # noqa: S603
                ["sops", "--decrypt", str(path)],  # noqa: S607
                capture_output=True,
                text=True,
                check=True,
                cwd=str(self._project_root),
            )
        except subprocess.CalledProcessError as e:
            raise SopsDecryptError(f"sops decrypt failed for {path}: {e.stderr.strip()}") from e
        data = yaml.safe_load(result.stdout)
        if not isinstance(data, dict):
            raise SopsDecryptError(f"expected dict in decrypted file, got {type(data)}")
        self._cache[tenant_id] = data
        return data

    def reload(self, tenant_id: str) -> dict[str, Any]:
        self._cache.pop(tenant_id, None)
        return self.load(tenant_id)
