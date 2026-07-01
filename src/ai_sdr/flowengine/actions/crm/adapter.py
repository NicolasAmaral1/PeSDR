"""CRMActionAdapter — single ActionAdapter (name='crm') that dispatches
to per-vendor backends based on `tenant.yaml > crm.provider`.

Why one adapter + N backends (instead of N adapters):
  - TreeFlow YAMLs reference `adapter: crm` once — vendor-agnostic.
  - Trocar de RD Station para HubSpot é uma mudança em tenant.yaml,
    sem reescrever TreeFlows.
  - Handler vocabulary stays canonical (`create_or_update_contact`),
    not vendor-specific (`rdstation_post_contacts`).
"""

from __future__ import annotations

import uuid
from typing import Any

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)
from ai_sdr.flowengine.actions.crm.factory import build_crm_backend
from ai_sdr.flowengine.actions.registry import register


class UnknownCRMHandlerError(Exception):
    """Raised when the TreeFlow YAML names a handler this backend doesn't support."""


@register
class CRMActionAdapter(ActionAdapter):
    name = "crm"

    def __init__(self, tenant_config: Any, secrets: dict[str, str]) -> None:
        super().__init__(tenant_config, secrets)
        if tenant_config.crm is None:
            raise ValueError(
                f"tenant {tenant_config.id!r}: missing `crm` block in tenant.yaml"
            )
        self.backend = build_crm_backend(
            tenant_config.crm.provider, tenant_config, secrets
        )

    async def execute(
        self, *, handler: str, params: dict[str, Any]
    ) -> ActionResult:
        if handler == "create_or_update_contact":
            return await self.backend.create_or_update_contact(
                lead_id=uuid.UUID(params["lead_id"]),
                contact=ContactCanonical(**params["contact"]),
            )
        if handler == "create_or_update_deal":
            return await self.backend.create_or_update_deal(
                lead_id=uuid.UUID(params["lead_id"]),
                contact_external_id=str(params["contact_external_id"]),
                deal=DealCanonical(**params["deal"]),
            )
        if handler == "update_deal_stage":
            stage: DealStage = params["stage"]
            return await self.backend.update_deal_stage(
                deal_external_id=str(params["deal_external_id"]),
                stage=stage,
            )
        if handler == "record_qualification_note":
            return await self.backend.record_qualification_note(
                contact_external_id=str(params["contact_external_id"]),
                note=str(params["note"]),
            )
        raise UnknownCRMHandlerError(
            f"handler {handler!r} not supported by CRM adapter"
        )
