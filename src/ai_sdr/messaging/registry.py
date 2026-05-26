"""Per-process cache of MessagingAdapter instances.

The factory is cheap (just constructor + secrets dict), but resolving
secrets requires SOPS decryption — expensive on cold path. Caching
(tenant_id, provider) → adapter avoids re-decrypting on every webhook.

This is a *singleton-style* registry per process; in the API layer it's
stored on app.state; in the worker process it's a module-level instance.
"""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.factory import build_messaging_adapter

if TYPE_CHECKING:
    from ai_sdr.models.tenant import Tenant
    from ai_sdr.secrets.sops_loader import SopsLoader
    from ai_sdr.tenant_loader.loader import TenantLoader


class AdapterRegistry:
    """Thread-safe cache of MessagingAdapter instances."""

    def __init__(
        self,
        tenant_loader: TenantLoader,
        sops_loader: SopsLoader,
    ) -> None:
        self._tenant_loader = tenant_loader
        self._sops_loader = sops_loader
        self._cache: dict[tuple[uuid.UUID, str], MessagingAdapter] = {}
        self._lock = threading.Lock()

    def get(self, tenant: Tenant, provider: str) -> MessagingAdapter:
        key = (tenant.id, provider)
        with self._lock:
            adapter = self._cache.get(key)
            if adapter is not None:
                return adapter

            tenant_cfg = self._tenant_loader.load(tenant.slug)
            if tenant_cfg.messaging is None:
                raise ValueError(f"tenant {tenant.slug} has no `messaging` block in tenant.yaml")
            if tenant_cfg.messaging.provider != provider:
                raise ValueError(
                    f"tenant {tenant.slug} configured provider="
                    f"{tenant_cfg.messaging.provider!r} but received {provider!r}"
                )
            secrets = self._sops_loader.load(tenant.slug)
            adapter = build_messaging_adapter(tenant_cfg.messaging, secrets)
            self._cache[key] = adapter
            return adapter

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
