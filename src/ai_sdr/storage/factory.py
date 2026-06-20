"""Dispatch a StorageAdapter from StorageConfig. Mirrors messaging/factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.fake import FakeStorageAdapter

_REGISTRY: dict[str, Callable[[StorageConfig, Mapping[str, str]], StorageAdapter]] = {}


def register_storage_provider(name: str):
    def _wrap(builder):
        if name in _REGISTRY:
            raise RuntimeError(f"storage provider already registered: {name}")
        _REGISTRY[name] = builder
        return builder

    return _wrap


@register_storage_provider("fake")
def _build_fake(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    return FakeStorageAdapter()


def build_storage_adapter(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    if cfg.provider == "minio" and "minio" not in _REGISTRY:
        from ai_sdr.storage import minio  # noqa: F401  (triggers registration)
    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown storage provider: {cfg.provider!r}")
    return builder(cfg, secrets)
