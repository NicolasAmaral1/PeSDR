"""CRMBackend ABC + registry pra vendors específicos (RD Station, HubSpot, etc).

Pattern: ABC + dict registry (similar a FormProviderAdapter, ActionAdapter, MessagingAdapter).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.flowengine.actions.crm.canonical import (
    ContactCanonical,
    DealCanonical,
    DealStage,
)
from ai_sdr.schemas.tenant_yaml import TenantConfig


# Registry: provider name → backend class
CRM_BACKENDS: dict[str, type["CRMBackend"]] = {}


def register_backend(cls: type["CRMBackend"]) -> type["CRMBackend"]:
    """Decorator pra registrar CRMBackend no CRM_BACKENDS dict.

    Uso:
        @register_backend
        class RDStationCRMBackend(CRMBackend):
            provider = "rdstation"
            ...
    """
    # TODO: implementação real (validação dup-name, missing attribute, etc)
    raise NotImplementedError("Fase B T2 — register_backend decorator")


class CRMBackend(ABC):
    """Implementação per-vendor dos handlers canônicos.

    Handlers padronizados retornam `ActionResult(external_id=str, detail=dict)`
    consumido pelo FE-03c worker (mark action_execution success).

    Idempotência: cada handler MUST ser safe pra retry. Backends usam:
    1. Lookup local em Lead.crm_refs (write-through cache).
    2. Lookup remoto por chave social (phone E.164).
    3. Create somente se 1 e 2 falharem.
    """

    provider: str  # class attribute, registry key

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def create_or_update_contact(
        self,
        *,
        lead_id: UUID,
        contact: ContactCanonical,
    ) -> ActionResult:
        """Upsert contato.

        Estratégia: lookup local → lookup remoto por phone → create.
        Returns ActionResult com external_id = vendor's contact id.
        """
        raise NotImplementedError

    @abstractmethod
    async def create_or_update_deal(
        self,
        *,
        lead_id: UUID,
        contact_external_id: str,
        deal: DealCanonical,
    ) -> ActionResult:
        """Upsert deal vinculado ao contato.

        Idempotência por (lead_id, deal.product) — não cria 2 deals do mesmo
        produto pro mesmo lead.
        """
        raise NotImplementedError

    @abstractmethod
    async def update_deal_stage(
        self,
        *,
        deal_external_id: str,
        stage: DealStage,
    ) -> ActionResult:
        """Move deal pra novo stage. Mapeia DealStage canônico → stage_id do vendor
        via tenant.yaml > crm.<provider>.stage_mapping.
        """
        raise NotImplementedError

    @abstractmethod
    async def record_qualification_note(
        self,
        *,
        contact_external_id: str,
        note: str,
    ) -> ActionResult:
        """Append nota textual ao contato.

        Útil pra registrar resumo da qualificação coletada via LLM (ex:
        "Faturamento mensal: R$ 40.000; tempo de mercado: 3 anos; ...").
        """
        raise NotImplementedError
