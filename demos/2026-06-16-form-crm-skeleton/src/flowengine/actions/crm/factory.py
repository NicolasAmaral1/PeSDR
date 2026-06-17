"""Factory pra resolver CRMBackend específico a partir do provider name.

Lookup em CRM_BACKENDS registry. Falha se provider não conhecido.
"""
from __future__ import annotations

from ai_sdr.flowengine.actions.crm.backend import CRMBackend, CRM_BACKENDS
from ai_sdr.flowengine.actions.crm.errors import UnknownBackendError
from ai_sdr.schemas.tenant_yaml import TenantConfig


def build_crm_backend(
    provider: str,
    tenant_config: TenantConfig,
    secrets: dict[str, str],
) -> CRMBackend:
    """Resolve backend class no CRM_BACKENDS registry e instancia.

    Args:
        provider: tenant.yaml > crm.provider (e.g., "rdstation").
        tenant_config: TenantConfig.
        secrets: dict de secrets decifrados.

    Returns:
        Instância do backend.

    Raises:
        UnknownBackendError: provider não registrado.
    """
    # TODO: implementação real
    # if provider not in CRM_BACKENDS:
    #     raise UnknownBackendError(
    #         f"CRM backend {provider!r} not registered. "
    #         f"Available: {sorted(CRM_BACKENDS.keys())}"
    #     )
    # cls = CRM_BACKENDS[provider]
    # return cls(tenant_config=tenant_config, secrets=secrets)
    raise NotImplementedError("Fase B T2 — build_crm_backend")
