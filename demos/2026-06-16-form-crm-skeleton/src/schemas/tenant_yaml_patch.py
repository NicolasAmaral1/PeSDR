"""Patch demonstrativo de tenant.yaml schema — delta da spec.

Esta é uma demonstração de QUE mudança vai ser feita em
`src/ai_sdr/schemas/tenant_yaml.py` REAL. Não é pra ser importado — é
referência visual pra Nicolas ver os 4 novos Pydantic models + 2 novos
campos em TenantConfig.

Mudanças:
- 4 novos Pydantic models: ProactiveFirstMessageConfig, FormProviderConfig,
  RDStationCRMConfig, CRMConfig
- 2 novos campos em TenantConfig: forms (dict), crm (optional)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self


# ─── Novos models pra forms ───────────────────────────────────────────────


class ProactiveFirstMessageConfig(BaseModel):
    """Configuração da mensagem proativa via HSM enviada pós form submission.

    Plano 9 já implementou `send_template`. Esta config conecta no worker
    `process_form_inbound`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    template_ref: str = Field(
        min_length=1,
        description="Nome do template aprovado no Meta Business Manager",
    )
    language: str = "pt_BR"
    params: list[str] = Field(
        default_factory=list,
        description="Templates Jinja2 renderizados em ordem pra {{1}}, {{2}}, ...",
    )


class FormProviderConfig(BaseModel):
    """Configuração per-provider de form ingestion.

    tenant.yaml > forms.respondi:
        enabled: true
        shared_secret_ref: secrets/respondi_webhook_secret
        start_treeflow: qualificacao_inicial
        field_mapping:
          qst_abc123: nome
          qst_def456: whatsapp_e164
        proactive_first_message:
          enabled: true
          template_ref: saudacao_proativa_v1
          ...
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    shared_secret_ref: str | None = None
    hmac_secret_ref: str | None = None  # pra providers que suportam HMAC (Typeform)
    start_treeflow: str = Field(min_length=1)
    field_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="form question_id → collected field name no TreeFlow",
    )
    proactive_first_message: ProactiveFirstMessageConfig | None = None

    @model_validator(mode="after")
    def _check_secret_ref(self) -> Self:
        if self.enabled and not (self.shared_secret_ref or self.hmac_secret_ref):
            raise ValueError(
                "forms.<provider>.enabled=true requires "
                "shared_secret_ref or hmac_secret_ref"
            )
        return self


# ─── Novos models pra CRM ─────────────────────────────────────────────────


class RDStationCRMConfig(BaseModel):
    """Configuração específica pra RD Station CRM."""

    model_config = ConfigDict(extra="forbid")

    refresh_token_ref: str = Field(min_length=1)
    client_id_ref: str = Field(min_length=1)
    client_secret_ref: str = Field(min_length=1)

    pipeline_id: str = Field(
        min_length=1,
        description="ID do pipeline onde todos os deals nascem",
    )
    stage_mapping: dict[Literal["open", "won", "lost"], str] = Field(
        description="Mapping DealStage canônico → stage_id do vendor"
    )
    custom_field_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="canonical field name → vendor custom_field_id",
    )


# class HubSpotCRMConfig(BaseModel):
#     """Futuro: HubSpot config."""
#     ...


class CRMConfig(BaseModel):
    """Configuração de CRM out — provider-agnostic.

    Provider field decide qual sub-block é mandatory.

    tenant.yaml > crm:
        provider: rdstation
        rdstation:
          refresh_token_ref: secrets/rdstation_refresh_token
          ...
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)

    rdstation: RDStationCRMConfig | None = None
    # hubspot: HubSpotCRMConfig | None = None  # future

    @model_validator(mode="after")
    def _check_provider_block_present(self) -> Self:
        provider_block = getattr(self, self.provider, None)
        if provider_block is None:
            raise ValueError(
                f"crm.provider={self.provider!r} requires "
                f"crm.{self.provider}: {{...}} block"
            )
        return self


# ─── Patch em TenantConfig ────────────────────────────────────────────────


class _TenantConfigPatch:
    """Pseudo-classe demonstrativa. NÃO IMPORTAR.

    Patch real em `schemas/tenant_yaml.py`:

    Adicionar ao corpo de `TenantConfig` (depois dos campos existentes):
    """

    forms: dict[str, FormProviderConfig] = Field(default_factory=dict)
    crm: CRMConfig | None = None

    # Validação adicional desejável (opcional):
    # - se tenant.crm está set, validar que pelo menos um TreeFlow do tenant
    #   tem on_collected com adapter='crm'. Caso contrário, warning no load.
    # - se algum forms.<provider>.start_treeflow não existe em tenants/<slug>/treeflows/,
    #   warning no load.
