"""Build the right CRMBackend for `tenant.crm.provider`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_sdr.flowengine.actions.crm.backend import CRMBackend

if TYPE_CHECKING:
    from ai_sdr.schemas.tenant_yaml import TenantConfig


class UnknownCRMProviderError(Exception):
    """Raised when tenant.crm.provider has no registered backend."""


def build_crm_backend(
    provider: str,
    tenant_config: TenantConfig,
    secrets: dict[str, str],
) -> CRMBackend:
    if provider == "rdstation":
        # Lazy import — keeps httpx out of paths that don't need it.
        from ai_sdr.flowengine.actions.crm.rdstation.backend import (
            RDStationCRMBackend,
        )

        if tenant_config.crm is None or tenant_config.crm.rdstation is None:
            raise UnknownCRMProviderError(
                "tenant.crm.rdstation block missing — required for provider='rdstation'"
            )
        return RDStationCRMBackend(tenant_config.crm.rdstation, secrets)

    raise UnknownCRMProviderError(f"unknown CRM provider: {provider!r}")
