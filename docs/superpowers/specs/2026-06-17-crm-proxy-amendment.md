# Spec Amendment: Form ingestion → CRM proxy (revisa Fase A da PR #20)

**Data:** 2026-06-17
**Status:** Proposta — aguardando revisão do Nicolas
**Tipo:** Spec amendment — atualização técnica de design, mantém spec PR #20 como histórico
**Autor:** Pedro Aranda (com Claude Code)
**Amenda:** [`2026-06-16-form-ingestion-and-crm-write-only-design.md`](./2026-06-16-form-ingestion-and-crm-write-only-design.md) (mergeada PR #20)
**Decisão raiz:** [`../notes/2026-06-17-form-ingestion-via-crm-proxy.md`](../notes/2026-06-17-form-ingestion-via-crm-proxy.md) (decision note)
**Não substitui:** ADR CRM [`2026-06-12-crm-posture-decision.md`](./2026-06-12-crm-posture-decision.md)

---

## 1. Contexto

Este documento é o **delta técnico** da spec PR #20. A justificativa conceitual está na [decision note](../notes/2026-06-17-form-ingestion-via-crm-proxy.md). Aqui foco em:

- O que muda no design técnico
- Schema novo / removido / mantido
- Contracts novos
- Plano de implementação revisado (tasks)

---

## 2. Mudança macro

### 2.1. Categoria de adapter mudou

| Spec PR #20 (antes) | Esta amendment (depois) |
|---|---|
| **5 bordas:** Messaging, Identity, HITL, Action, **Form** | **5 bordas:** Messaging, Identity, HITL, Action, **CRM Inbound** |

A 5ª borda nova **não é "Form"** (formulário externo). É **"CRM Inbound"** (entrada via webhook do CRM externo). Mais alinhada com o ADR CRM macro.

### 2.2. Webhook URL pattern mudou

| Antes | Depois |
|---|---|
| `POST /webhooks/{tenant_slug}/form/{provider}` | `POST /webhooks/{tenant_slug}/crm/{provider}` |
| Exemplo: `.../form/respondi?secret=...` | Exemplo: `.../crm/rdstation` (HMAC header) |

### 2.3. Subsistema mudou

| Antes (criar) | Depois (criar) |
|---|---|
| `src/ai_sdr/forms/` | `src/ai_sdr/crm/inbound/` |

Coloca embaixo de `crm/` em vez de top-level pra agrupar com a Fase B (CRM out) e antecipar a estrutura da Fase 3.

### 2.4. Tabela mudou

| Antes | Depois |
|---|---|
| `inbound_form_submissions` | `inbound_crm_events` |

Schema similar mas com campos específicos pra eventos de CRM (event_type, contact_external_id, etc).

---

## 3. Novos contracts

### 3.1. `CRMInboundAdapter` ABC

```python
# src/ai_sdr/crm/inbound/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, model_validator

CRMEventType = Literal[
    "contact_created",
    "contact_updated",
    "deal_created",
    "deal_stage_changed",
    # outros eventos relevantes — extensível
]


class LeadIdentifier(BaseModel):
    """Como o Lead será resolvido (find-or-create) por crm/inbound/ingest.
    Compartilhado com WhatsApp ingest (Plano 6 futuro unifica).
    """
    whatsapp_e164: str | None = None
    email: str | None = None
    external_label: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self):
        if not any([self.whatsapp_e164, self.email, self.external_label]):
            raise ValueError("LeadIdentifier requires at least one identifier")
        return self


@dataclass(frozen=True)
class IngestedCRMEvent:
    """Evento CRM normalizado — independente de vendor.

    Saída do CRMInboundAdapter.handle_webhook. Consumido pelo route handler
    + worker job process_crm_event.

    Quando Fase 3 entrar, sync engine consome este mesmo shape.
    """
    external_id: str
    """ID do evento no CRM externo (idempotency key)."""

    event_type: CRMEventType
    contact_external_id: str | None
    deal_external_id: str | None
    lead_identifier: LeadIdentifier
    field_values: dict[str, Any]
    source_meta: dict[str, Any]
    """Origin: {'source': 'integration'|'manual', 'tags': [...], 'utms': {...}}"""
    occurred_at_iso: str


class CRMInboundAdapter(ABC):
    """Boundary entre webhook CRM externo e runtime PeSDR.

    Pure: zero conhecimento de DB. Só normaliza payload + valida assinatura.

    Construção: factory injeta tenant_config + secrets. Adapter NÃO carrega
    secrets sozinho.
    """
    name: str  # class attribute — registry key

    def __init__(self, tenant_config, secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def handle_webhook(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
    ) -> IngestedCRMEvent | None:
        """Valida + parseia + normaliza payload do CRM webhook.

        Returns:
            IngestedCRMEvent se evento deve criar/atualizar Lead/Talk.
            None se evento deve ser ignorado (ex: contact criado manualmente,
            deal de outro pipeline, etc).

        Raises:
            SignatureError: HMAC inválido.
            MalformedPayload: shape inesperado.
        """
        raise NotImplementedError
```

### 3.2. `RDStationCRMInboundAdapter` (primeiro impl)

```python
# src/ai_sdr/crm/inbound/rdstation.py
@register_crm_inbound
class RDStationCRMInboundAdapter(CRMInboundAdapter):
    name = "rdstation"

    async def handle_webhook(self, raw_body, headers, query_params):
        # 1. Valida HMAC (X-RD-Signature ou similar — confirmar)
        self._validate_signature(raw_body, headers)

        # 2. Parse JSON
        payload = self._parse_json(raw_body)

        # 3. Filtra origem:
        #    - source: 'integration' → criar Talk
        #    - source: 'manual' → ignorar (retornar None)
        if not self._is_from_integration(payload):
            return None  # ignorado silenciosamente

        # 4. Filtra event_type relevante
        event_type = payload.get("event_type")
        if event_type not in ("contact_created", "deal_created"):
            return None  # eventos não interessantes pra abrir Talk

        # 5. Normaliza phone E.164 (usa _normalize_e164 compartilhado)
        contact = payload.get("contact", {})
        phone_raw = contact.get("phone")
        phone_e164 = normalize_e164(phone_raw, default_region="BR")

        # 6. Extrai field_values do contact
        field_values = self._extract_field_values(contact)

        # 7. Constrói IngestedCRMEvent
        return IngestedCRMEvent(
            external_id=payload["event_id"],
            event_type=event_type,
            contact_external_id=contact.get("id"),
            deal_external_id=payload.get("deal", {}).get("id"),
            lead_identifier=LeadIdentifier(whatsapp_e164=phone_e164),
            field_values=field_values,
            source_meta={
                "source": payload.get("source", "unknown"),
                "tags": contact.get("tags", []),
                "campaign": contact.get("campaign"),
                "form_origin": contact.get("origin"),  # se disponível
            },
            occurred_at_iso=payload["occurred_at"],
        )
```

### 3.3. Worker job `process_crm_event` (Fase 1 — fino, refatorável)

```python
# src/ai_sdr/worker/jobs/crm_inbound.py
async def process_crm_event(ctx, event_id_str: str) -> None:
    """arq job — processa 1 evento CRM.

    Fase 1: cria Talk direto a partir do evento.
    Fase 3+: vai delegar pro sync engine (ver decision note §6.2 + §8).
    """
    event_id = UUID(event_id_str)

    async with session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))

        ev = await session.get(InboundCRMEvent, event_id)
        if ev is None:
            log.info("crm_event.not_found", event_id=event_id_str)
            return

        if ev.status != "queued":
            return  # already processed

        await set_tenant_context(session, ev.tenant_id)

        tenant_loader = ctx["tenant_loader"]
        tenant = await tenant_loader.load_by_id(ev.tenant_id)
        lead = await session.get(Lead, ev.lead_id)

        crm_inbound_cfg = tenant.crm.inbound[ev.provider]
        treeflow_id = crm_inbound_cfg.start_treeflow

        talk = await create_talk_with_state(
            session=session,
            tenant=tenant,
            lead=lead,
            treeflow_id=treeflow_id,
            preloaded_collected=ev.field_values,
        )

        # Lead.crm_refs sync — registra o contact_id do RD Station já capturado
        if ev.contact_external_id:
            await set_crm_ref(
                session, lead.id, "rdstation", "contact_id", ev.contact_external_id
            )

        # Send proactive HSM
        if crm_inbound_cfg.proactive_first_message and crm_inbound_cfg.proactive_first_message.enabled:
            await _send_proactive_hsm(session, tenant, lead, talk, crm_inbound_cfg)

        ev.status = "processed"
        ev.processed_at = utcnow()
        await session.commit()
```

---

## 4. Schema changes

### 4.1. Migration revisada — `0030_crm_inbound_and_crm_refs.py`

```python
def upgrade() -> None:
    # 1. Lead.crm_refs JSONB (PRESERVADA da spec original)
    op.add_column("leads",
        sa.Column("crm_refs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))
    )
    op.create_index("ix_leads_crm_refs_gin", "leads", ["crm_refs"], postgresql_using="gin")

    # 2. inbound_crm_events table (substitui inbound_form_submissions)
    op.create_table("inbound_crm_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),  # 'rdstation', 'hubspot', etc
        sa.Column("external_id", sa.Text(), nullable=False),  # event_id do CRM externo
        sa.Column("event_type", sa.Text(), nullable=False),  # 'contact_created' etc
        sa.Column("contact_external_id", sa.Text()),
        sa.Column("deal_external_id", sa.Text()),
        sa.Column("lead_id", UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("raw", JSONB(), nullable=False),
        sa.Column("field_values", JSONB(), nullable=False),
        sa.Column("source_meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.Text()),
        sa.CheckConstraint(
            "status IN ('queued', 'processed', 'skipped_ignored', 'skipped_dedupe', 'error')",
            name="ck_inbound_crm_status",
        ),
    )

    # Dedup index — UNIQUE (tenant_id, provider, external_id)
    op.create_index(
        "uq_inbound_crm_extid",
        "inbound_crm_events",
        ["tenant_id", "provider", "external_id"],
        unique=True,
    )

    # Partial index pra worker scan
    op.create_index(
        "ix_inbound_crm_lead_status",
        "inbound_crm_events",
        ["lead_id", "status"],
        postgresql_where=sa.text("status IN ('queued', 'error')"),
    )

    # RLS
    op.execute("ALTER TABLE inbound_crm_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_crm_events FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON inbound_crm_events
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)
```

**Diferenças vs migration original:**
- `inbound_form_submissions` → `inbound_crm_events`
- Adiciona `event_type`, `contact_external_id`, `deal_external_id`, `source_meta`
- Adiciona valor `skipped_ignored` no enum status (pra eventos filtrados por origem)
- Renomeia `submitted_at` → `occurred_at` (semântica de evento, não submissão)

### 4.2. Schema tenant.yaml

```yaml
# tenants/manoela-mentora/tenant.yaml
crm:
  provider: rdstation                                # vendor genérico

  # ─── Bloco INBOUND (NOVO — entrada via webhook CRM) ──────────────
  inbound:
    rdstation:
      enabled: true
      hmac_secret_ref: secrets/rdstation_webhook_secret
      start_treeflow: qualificacao_inicial

      # Filtragem: só eventos vindos de integração (não criados manualmente)
      origin_filter:
        accept_sources: ["integration"]              # filtros por field do payload
        accept_tags: []                              # ou tags específicas, se aplicável
        ignore_manual: true                          # ignora contacts criados manualmente

      # Eventos a escutar — só os relevantes pra abrir/atualizar Talk
      events:
        - contact_created
        # - deal_stage_changed  # futuro, pra disparar follow-up por stage

      proactive_first_message:
        enabled: true
        template_ref: "saudacao_mentoria_v1"
        language: pt_BR
        params:
          - "{{ collected.nome | default('') | capitalize }}"

  # ─── Bloco OUTBOUND (preservado da spec PR #20) ─────────────────
  rdstation:
    refresh_token_ref: secrets/rdstation_refresh_token
    client_id_ref: secrets/rdstation_client_id
    client_secret_ref: secrets/rdstation_client_secret
    pipeline_id: "<rdstation_pipeline_id>"
    stage_mapping:
      open: "<rdstation_stage_id_open>"
      won: "<rdstation_stage_id_won>"
      lost: "<rdstation_stage_id_lost>"
    custom_field_mapping:
      faturamento_mensal: "<rdstation_cf_id>"
```

Note que `crm` agora tem 2 blocos: `inbound` (webhook listener) + provider-specific (`rdstation`, ação outbound). Coexistem.

**REMOVIDO:** bloco `forms:` (era da spec PR #20).

### 4.3. Pydantic models

```python
# src/ai_sdr/schemas/tenant_yaml.py — additions

class OriginFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_sources: list[str] = Field(default_factory=lambda: ["integration"])
    accept_tags: list[str] = Field(default_factory=list)
    ignore_manual: bool = True


class CRMInboundProviderConfig(BaseModel):
    """Per-provider config pra entrada de CRM via webhook."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    hmac_secret_ref: str | None = None
    start_treeflow: str = Field(min_length=1)
    origin_filter: OriginFilter = Field(default_factory=OriginFilter)
    events: list[CRMEventType] = Field(default_factory=lambda: ["contact_created"])
    proactive_first_message: ProactiveFirstMessageConfig | None = None

    @model_validator(mode="after")
    def _check_secret_ref(self):
        if self.enabled and not self.hmac_secret_ref:
            raise ValueError("crm.inbound.<provider>.enabled=true requires hmac_secret_ref")
        return self


class CRMConfig(BaseModel):
    """Configuração de CRM — entrada + saída."""
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1)

    # NOVO — entrada
    inbound: dict[str, CRMInboundProviderConfig] = Field(default_factory=dict)

    # Provider-specific (saída — preservado da spec PR #20)
    rdstation: RDStationCRMConfig | None = None
    # hubspot: HubSpotCRMConfig | None = None  # future
```

**REMOVIDO:** `FormProviderConfig` (da spec PR #20). `forms:` deixa de existir como bloco.

---

## 5. Plano de implementação revisado (substitui §8 da spec PR #20)

### 5.1. Estrutura final de pastas esperada

```
src/ai_sdr/
├── crm/                                           # NOVO subsistema (agrupa entrada e saída)
│   ├── __init__.py
│   ├── inbound/                                    # ENTRADA
│   │   ├── __init__.py
│   │   ├── base.py                                 # CRMInboundAdapter ABC + IngestedCRMEvent
│   │   ├── registry.py
│   │   ├── factory.py
│   │   ├── errors.py
│   │   ├── ingest.py                               # find_or_create_lead_by_crm_event
│   │   └── rdstation.py                            # RDStationCRMInboundAdapter
│   └── (futuro Fase 3) tables.py, sync_engine.py, etc.
│
├── flowengine/actions/crm/                         # SAÍDA (preservado da spec PR #20)
│   ├── adapter.py                                  # CRMActionAdapter
│   ├── canonical.py                                # ContactCanonical, DealCanonical
│   ├── backend.py                                  # CRMBackend ABC
│   ├── factory.py
│   ├── errors.py
│   └── rdstation/
│       ├── backend.py
│       ├── oauth.py
│       └── client.py
│
├── api/routes/
│   └── crm_inbound.py                              # NOVO — webhook CRM
│
├── worker/jobs/
│   └── crm_inbound.py                              # NOVO — process_crm_event
│
├── models/
│   ├── inbound_crm_event.py                        # NOVO ORM
│   └── lead.py                                     # MODIFICADO — add crm_refs
│
├── repositories/
│   └── inbound_crm_event_repository.py             # NOVO
│
├── schemas/
│   └── tenant_yaml.py                              # MODIFICADO — CRMConfig
│
├── flowengine/actions/templating.py                # MODIFICADO — add lead.crm_refs
│
└── helpers/                                        # NOVO
    └── phone.py                                    # normalize_e164() compartilhado

migrations/versions/
└── 0030_crm_inbound_and_crm_refs.py                # NOVO (renomeada vs spec PR #20)
└── 0031_proactive_template_unapproved_reason.py    # NOVO (decisão #8 Nicolas)

tenants/manoela-mentora/                            # Fase C
├── tenant.yaml                                     # MODIFICADO — add crm.inbound + crm.rdstation
├── secrets.enc.yaml                                # MODIFICADO — add 4 chaves novas
└── treeflows/qualificacao_inicial.yaml             # MODIFICADO — add on_collected: crm
```

### 5.2. Tasks da Fase A revisada (11 tasks)

| Task | Escopo | Estimativa |
|---|---|---|
| **A1** | Migration `0030_crm_inbound_and_crm_refs.py` (DDL completo + RLS) | S |
| **A2** | Migration `0031_proactive_template_unapproved_reason.py` (enum add) | S |
| **A3** | Schema tenant.yaml — `CRMInboundProviderConfig`, `OriginFilter`, `ProactiveFirstMessageConfig`, atualiza `CRMConfig` | S |
| **A4** | Helper `src/ai_sdr/helpers/phone.py` — `normalize_e164()` compartilhado (decisão #9 Nicolas) | S |
| **A5** | Refator do webhook WhatsApp pra usar `normalize_e164()` (decisão #9 Nicolas) — backfill não necessário (piloto não está em prod) | M |
| **A6** | Model + Repository `InboundCRMEvent` | S |
| **A7** | Subsistema `crm/inbound/`: ABC + registry + factory + errors | M |
| **A8** | `RDStationCRMInboundAdapter` (HMAC verify, parsing, origin filter) | M |
| **A9** | Helper `find_or_create_lead_by_crm_event` + `create_talk_with_state` em `crm/inbound/ingest.py` | M |
| **A10** | Route `POST /webhooks/{slug}/crm/{provider}` em `api/routes/crm_inbound.py` | S |
| **A11** | Worker job `worker/jobs/crm_inbound.py` (process_crm_event) + lógica `__prefilled_fields__` | M |
| **A12** | Tests unit + integration + e2e fixture (payload RD Station real) | M |
| **A13** | CLAUDE.md ganha seção "CRM inbound (Plano 7a)" | S |

### 5.3. Fase B inalterada

12 tasks da spec PR #20 (CRM RD Station write-only) **permanecem como estão**. Sem mudança.

### 5.4. Fase C revisada (substitui §8.3 da spec PR #20)

| Task | Escopo |
|---|---|
| **C1** | Pedro/Lana configura no painel Respondi: clica "Conectar RD Station CRM" → autoriza |
| **C2** | Pedro/Lana captura no painel RD Station: `pipeline_id`, 3× `stage_id`, N× `custom_field_id` |
| **C3** | Pedro/Lana cria app OAuth no RD Station, captura `client_id` + `client_secret` |
| **C4** | Pedro/Lana configura webhook URL no RD Station: `https://sdr.luminai.ia.br/webhooks/manoela-mentora/crm/rdstation` + secret HMAC compartilhado |
| **C5** | Pedro/Lana confirma origem dos eventos via integração (response questões §14 decision note) |
| **C6** | Pedro/Lana cria templates HSM aprovados no Meta (`saudacao_mentoria_v1`) |
| **C7** | Pedro atualiza `tenant.yaml` com blocos `crm.inbound.rdstation` + `crm.rdstation` |
| **C8** | Pedro cifra `secrets.enc.yaml` com novas chaves |
| **C9** | Pedro atualiza TreeFlow `qualificacao_inicial.yaml` v0.3.0 com `on_collected: crm` |
| **C10** | Pedro/Claude rodam smoke test E2E manual |

---

## 6. Open questions adicionais (não estavam na spec PR #20)

Pendências introduzidas por esta amendment:

| # | Pergunta | Resolução |
|---|---|---|
| **OQ-A1** | RD Station documenta como diferenciar evento "criado via integração" vs "criado manualmente"? | Pedro investiga ao configurar webhook (Fase C5) |
| **OQ-A2** | HMAC nos webhooks RD Station ou só URL secreta? | Pedro confirma na documentação RD Station |
| **OQ-A3** | RD Station retry policy em webhook failed? | Pedro confirma na documentação |
| **OQ-A4** | Múltiplos forms Respondi (Mentoria + Aceleradora) podem ser distinguidos no contact no RD Station? Via tag, custom field, ou source? | Pedro investiga ao configurar Aceleradora |
| **OQ-A5** | Critério de filtro do `origin_filter` (qual campo do payload examinar)? | Depende de OQ-A1 |

Mantidas as 2 pendências da spec PR #20 (não bloqueiam Fase A):
- #5 OAuth refresh rotation
- #12 Smoke sandbox

---

## 7. Critérios de aceitação desta amendment

Pra ser mergeada em main:

- [ ] Nicolas revisou e aprovou (review obrigatório do ruleset main)
- [ ] Decision note ([2026-06-17-form-ingestion-via-crm-proxy.md](../notes/2026-06-17-form-ingestion-via-crm-proxy.md)) lida e endossada
- [ ] CLAUDE.md atualizado (PR companion)
- [ ] Pedro responde OQ-A1 (filtragem de origem) com base no painel real

---

## 8. Versionamento desta spec

Esta é a **v1 da amendment**. Se Nicolas pedir ajustes durante review, abrimos v2 com diff explícito. Histórico via Git.

---

**Fim do Spec Amendment.**

> **Próximo passo após aprovação:** skill `writing-plans` gera o plan executável da Fase A revisada (13 tasks) em `docs/superpowers/plans/2026-06-17-crm-inbound-fase-a.md`.
