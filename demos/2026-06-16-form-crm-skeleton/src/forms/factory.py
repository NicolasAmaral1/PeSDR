"""Factory pra construir FormProviderAdapter a partir de nome + tenant + secrets.

Espelha `messaging/factory.py`.
"""
from __future__ import annotations

from ai_sdr.forms.base import FormProviderAdapter
from ai_sdr.forms.errors import UnknownFormProviderError
from ai_sdr.forms.registry import FORM_PROVIDERS
from ai_sdr.schemas.tenant_yaml import TenantConfig


def build_form_adapter(
    name: str,
    tenant_config: TenantConfig,
    secrets: dict[str, str],
) -> FormProviderAdapter:
    """Resolve adapter class no registry e instancia.

    Args:
        name: provider name (e.g., "respondi"). Deve existir em FORM_PROVIDERS.
        tenant_config: TenantConfig carregado via TenantLoader.
        secrets: dict de secrets decifrados via SopsLoader.

    Returns:
        Instância do adapter, pronta pra handle_submission.

    Raises:
        UnknownFormProviderError: name não está no registry.
    """
    # TODO: implementação real
    # if name not in FORM_PROVIDERS:
    #     raise UnknownFormProviderError(
    #         f"form provider {name!r} not registered. "
    #         f"Available: {sorted(FORM_PROVIDERS.keys())}"
    #     )
    # cls = FORM_PROVIDERS[name]
    # return cls(tenant_config=tenant_config, secrets=secrets)
    raise NotImplementedError("Fase A T4 — factory")
