"""FormProviderAdapter ABC + IngestedFormSubmission dataclass + LeadIdentifier.

Contract pra a 4ª borda nova do PeSDR. Espelha o pattern de `messaging/base.py`
mas com semântica de entrada de form (vs entrada de mensagem).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, model_validator

# TYPE_CHECKING import pra evitar ciclo — TenantConfig será resolvido em runtime
from ai_sdr.schemas.tenant_yaml import TenantConfig


class LeadIdentifier(BaseModel):
    """Como o Lead será resolvido (find-or-create) por `forms.ingest`.

    Pelo menos UM dos campos deve ser não-None. Política de matching:
    - whatsapp_e164 é primary key (matching exato + tenant_id)
    - email é fallback (mas NÃO usado pra matching no piloto Manoela)
    - external_label é fallback de último recurso (usado pelo CLI simulate)
    """
    whatsapp_e164: str | None = None
    email: str | None = None
    external_label: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "LeadIdentifier":
        """TODO: implementação real validará que pelo menos 1 campo é não-None."""
        # if not any([self.whatsapp_e164, self.email, self.external_label]):
        #     raise ValueError(
        #         "LeadIdentifier requires at least one of "
        #         "whatsapp_e164/email/external_label"
        #     )
        # return self
        raise NotImplementedError("LeadIdentifier validator — implement in Fase A T2")


@dataclass(frozen=True)
class IngestedFormSubmission:
    """Output normalizado do FormProviderAdapter.handle_submission.

    Construído pelo adapter específico (RespondiFormAdapter, etc) a partir do
    raw payload + tenant config. Consumido pelo route handler + worker job.

    Attributes:
        external_id: id provider-native (dedup key). Respondi: respondent_id.
        submitted_at_iso: timestamp ISO 8601 da submissão.
        lead_identifier: como achar/criar o Lead.
        field_values: dict de campos JÁ MAPEADOS via tenant.yaml field_mapping.
                      Chaves são vocabulário PeSDR (e.g., "nome", "faturamento_mensal").
        source_meta: metadata pra audit (form_id, utms, score, status, etc).
                     Nunca usado pra lógica — só inspeção humana.
    """
    external_id: str
    submitted_at_iso: str
    lead_identifier: LeadIdentifier
    field_values: dict[str, Any]
    source_meta: dict[str, Any] = field(default_factory=dict)


class FormProviderAdapter(ABC):
    """Boundary entre webhook provider externo e runtime PeSDR.

    Pure: zero conhecimento de DB (leads/tenants tables). Só normaliza payload
    + valida assinatura/secret.

    Construção: factory injeta tenant_config + secrets. Adapter NÃO carrega
    secrets sozinho.

    Idempotency: não responsabilidade do adapter — webhook handler + DB UNIQUE
    cuidam (mesma submissão chega 2x → ON CONFLICT DO NOTHING).
    """

    name: str  # class attribute — registry key

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def handle_submission(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        """Valida + parseia + normaliza payload do form provider.

        Args:
            raw_body: body bruto do POST.
            headers: HTTP headers.
            query_params: query string (Respondi usa pro shared_secret).

        Returns:
            IngestedFormSubmission normalizado.

        Raises:
            SignatureError: assinatura/secret inválido.
            MalformedPayload: shape inesperado, campo obrigatório ausente,
                              phone não-normalizável, etc.
        """
        raise NotImplementedError
