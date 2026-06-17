"""CRMActionAdapter — ActionAdapter genérico que despacha pra backend específico.

Registrado no FE-03c ACTION_ADAPTERS registry com name="crm". TreeFlow YAML
declara `adapter: crm` (vendor-agnostic) — backend resolvido em runtime via
`tenant.yaml > crm.provider`.

Handlers suportados (delega 1:1 pro backend):
- create_or_update_contact
- create_or_update_deal
- update_deal_stage
- record_qualification_note

Open question §11.4 da spec: o que fazer se tenant não tem `crm:` block mas
TreeFlow YAML usa `adapter: crm`? Default proposto: skipar com warning
(não bloqueia turn). Decisão final do Nicolas.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)
from ai_sdr.flowengine.actions.crm.errors import UnknownHandlerError
from ai_sdr.flowengine.actions.crm.factory import build_crm_backend
from ai_sdr.flowengine.actions.registry import register
from ai_sdr.schemas.tenant_yaml import TenantConfig


@register  # registra no ACTION_ADAPTERS dict do FE-03c
class CRMActionAdapter(ActionAdapter):
    """ActionAdapter genérico vendor-agnostic pra CRM.

    Construção: __init__ resolve backend específico via tenant.yaml > crm.provider.
    Execução: despacha pra método do backend baseado em handler string.
    """

    name = "crm"

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]) -> None:
        super().__init__(tenant_config, secrets)
        if not getattr(tenant_config, "crm", None):
            raise ValueError(
                f"tenant {tenant_config.id!r} has 'adapter: crm' in TreeFlow but "
                f"no `crm:` block in tenant.yaml. Add `crm.provider` and "
                f"provider-specific block to enable CRM actions."
            )
        self.backend = build_crm_backend(
            tenant_config.crm.provider, tenant_config, secrets
        )

    async def execute(
        self,
        *,
        handler: str,
        params: dict[str, Any],
    ) -> ActionResult:
        """Despacha handler pro backend method correspondente.

        Args:
            handler: string do TreeFlow YAML (e.g., "create_or_update_contact").
            params: dict já renderizado pelo dispatcher FE-03c (Jinja2 sandbox).

        Returns:
            ActionResult retornado pelo backend.

        Raises:
            UnknownHandlerError: handler não suportado.
            (vários do backend — AuthError, ValidationError, etc)
        """
        # TODO: implementação real
        # if handler == "create_or_update_contact":
        #     return await self.backend.create_or_update_contact(
        #         lead_id=UUID(params["lead_id"]),
        #         contact=ContactCanonical(**params["contact"]),
        #     )
        # elif handler == "create_or_update_deal":
        #     return await self.backend.create_or_update_deal(
        #         lead_id=UUID(params["lead_id"]),
        #         contact_external_id=params["contact_external_id"],
        #         deal=DealCanonical(**params["deal"]),
        #     )
        # elif handler == "update_deal_stage":
        #     return await self.backend.update_deal_stage(
        #         deal_external_id=params["deal_external_id"],
        #         stage=params["stage"],
        #     )
        # elif handler == "record_qualification_note":
        #     return await self.backend.record_qualification_note(
        #         contact_external_id=params["contact_external_id"],
        #         note=params["note"],
        #     )
        # raise UnknownHandlerError(
        #     f"CRMActionAdapter: handler {handler!r} not supported"
        # )
        raise NotImplementedError("Fase B T3 — CRMActionAdapter.execute dispatch")
