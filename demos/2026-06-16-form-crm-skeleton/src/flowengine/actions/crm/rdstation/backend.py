"""RDStationCRMBackend — primeiro impl real de CRMBackend.

Implementa os 4 handlers canônicos batendo na RD Station CRM API v1.

Pattern de idempotência (write-through cache):
1. Lookup local em Lead.crm_refs (DB)
2. Lookup remoto por phone (RD Station search)
3. Create somente se 1 e 2 falharem

Cada handler escreve external_id de volta em Lead.crm_refs.<key> via
UPDATE atômico (UPDATE ... SET crm_refs = jsonb_set(...)).
"""
from __future__ import annotations

from uuid import UUID

import structlog

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.flowengine.actions.crm.backend import CRMBackend, register_backend
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)
from ai_sdr.flowengine.actions.crm.errors import UnknownHandlerError
from ai_sdr.flowengine.actions.crm.rdstation.client import RDStationClient
from ai_sdr.flowengine.actions.crm.rdstation.oauth import RDStationOAuth
from ai_sdr.schemas.tenant_yaml import TenantConfig

log = structlog.get_logger(__name__)


@register_backend
class RDStationCRMBackend(CRMBackend):
    """RD Station CRM API v1 backend.

    Config consumido (tenant.yaml > crm.rdstation):
    - refresh_token_ref, client_id_ref, client_secret_ref (OAuth)
    - pipeline_id (todos os deals nascem aqui)
    - stage_mapping (DealStage canônico → stage_id do vendor)
    - custom_field_mapping (campo canônico → custom_field_id do vendor)
    """

    provider = "rdstation"

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]) -> None:
        super().__init__(tenant_config, secrets)
        cfg = tenant_config.crm.rdstation

        # Decifrar refs (prefix "secrets/" → bare name no SopsLoader)
        self.oauth = RDStationOAuth(
            refresh_token=secrets[cfg.refresh_token_ref.removeprefix("secrets/")],
            client_id=secrets[cfg.client_id_ref.removeprefix("secrets/")],
            client_secret=secrets[cfg.client_secret_ref.removeprefix("secrets/")],
        )
        self.client = RDStationClient(self.oauth)
        self.pipeline_id = cfg.pipeline_id
        self.stage_mapping = cfg.stage_mapping
        self.custom_field_mapping = cfg.custom_field_mapping

    # ─── Handlers canônicos ───────────────────────────────────────────────

    async def create_or_update_contact(
        self,
        *,
        lead_id: UUID,
        contact: ContactCanonical,
    ) -> ActionResult:
        """Upsert contact.

        Roteiro:
        1. local_id = await self._lookup_local_ref(lead_id, "contact_id")
           if local_id: return await self._do_update_contact(local_id, contact)
        2. if contact.phones:
             remote = await self.client.search_contact_by_phone(contact.phones[0])
             if remote:
                 await self._persist_local_ref(lead_id, "contact_id", remote["id"])
                 return await self._do_update_contact(remote["id"], contact)
        3. body = self._build_contact_body(contact)
           created = await self.client.create_contact(body)
           await self._persist_local_ref(lead_id, "contact_id", created["id"])
           log.info("crm.rdstation.contact_created", ...)
           return ActionResult(external_id=created["id"], detail={"created": True})
        """
        raise NotImplementedError("Fase B T7 — create_or_update_contact")

    async def create_or_update_deal(
        self,
        *,
        lead_id: UUID,
        contact_external_id: str,
        deal: DealCanonical,
    ) -> ActionResult:
        """Upsert deal vinculado ao contato.

        Idempotência por (lead_id, deal.product) — chave em Lead.crm_refs:
            f"deal_id_{deal.product.lower().replace(' ', '_')}"
        """
        raise NotImplementedError("Fase B T8 — create_or_update_deal")

    async def update_deal_stage(
        self,
        *,
        deal_external_id: str,
        stage: DealStage,
    ) -> ActionResult:
        """PATCH /deals/{id} com novo deal_stage.

        Mapeia DealStage canônico ("open"|"won"|"lost") → stage_id do vendor
        via self.stage_mapping.
        """
        raise NotImplementedError("Fase B T9 — update_deal_stage")

    async def record_qualification_note(
        self,
        *,
        contact_external_id: str,
        note: str,
    ) -> ActionResult:
        """Append comment ao contact (ou ao deal mais recente — TBD).

        Open question (minor): comment vai em contact ou em deal? Pra Manoela,
        provavelmente deal é mais útil (rastreável pela equipe de vendas).
        """
        raise NotImplementedError("Fase B T9 — record_qualification_note")

    # ─── Helpers privados (Lead.crm_refs writeback) ────────────────────────

    async def _lookup_local_ref(self, lead_id: UUID, ref_key: str) -> str | None:
        """SELECT crm_refs->'rdstation'->>ref_key FROM leads WHERE id = lead_id."""
        raise NotImplementedError("Fase B T10 — _lookup_local_ref")

    async def _persist_local_ref(
        self, lead_id: UUID, ref_key: str, external_id: str
    ) -> None:
        """UPDATE leads SET crm_refs = jsonb_set(crm_refs, '{rdstation, <key>}', '<id>')
        WHERE id = lead_id

        Atomic via advisory lock pra prevenir corrupção em escrita concorrente.
        """
        raise NotImplementedError("Fase B T10 — _persist_local_ref")

    # ─── Helpers de body building ─────────────────────────────────────────

    def _build_contact_body(self, contact: ContactCanonical) -> dict:
        """Traduz ContactCanonical → JSON body da RD Station API v1."""
        # return {
        #     "contact": {
        #         "name": contact.name,
        #         "emails": [{"email": e} for e in contact.emails],
        #         "phones": [{"phone": p, "type": "cellphone"} for p in contact.phones],
        #     }
        #     | {self.custom_field_mapping.get(k, k): v for k, v in contact.custom_fields.items()},
        # }
        raise NotImplementedError("Fase B T7 — _build_contact_body")

    def _build_deal_body(
        self, contact_external_id: str, deal: DealCanonical
    ) -> dict:
        """Traduz DealCanonical → JSON body."""
        raise NotImplementedError("Fase B T8 — _build_deal_body")
