# Decision Note: Form ingestion via CRM proxy (Fase 1 do ADR CRM)

**Data:** 2026-06-17
**Status:** Proposta — aguardando revisão do Nicolas
**Tipo:** Decision Note (não ADR completa — registro de revisão de implementação dentro da Fase 1 do ADR CRM)
**Autor:** Pedro Aranda (decisão coordenada com Claude Code)
**Revisa:** [`2026-06-16-form-ingestion-and-crm-write-only-design.md`](../specs/2026-06-16-form-ingestion-and-crm-write-only-design.md) (spec mergeada via PR #20 em 2026-06-17)
**Compatível com:** [`2026-06-12-crm-posture-decision.md`](../specs/2026-06-12-crm-posture-decision.md) (ADR CRM macro)
**Não substitui:** nenhum ADR — só ajusta a implementação concreta da Fase 1

---

## 1. TL;DR

Em 2026-06-17, durante o início da implementação da Fase 1 do ADR CRM, Pedro Aranda **confirmou capacidade de integração nativa** entre Respondi e RD Station CRM (botão "Conectar" disponível no painel Respondi sob "RD Station CRM PRO").

Esta nota documenta a decisão de **substituir o `FormProviderAdapter` (caminho de entrada via webhook Respondi direto, conforme spec PR #20) pelo `CRMInboundAdapter` (caminho de entrada via webhook RD Station)** na implementação da Fase 1, **sem alterar o ADR macro** e **sem criar débito técnico pra Fase 3**.

**Resultado prático:**

- Eliminamos `field_mapping` por `question_id` (zero hard code de form)
- Eliminamos duplicação de dados (RD Station é única superfície de criação)
- Antecipamos o pattern "PeSDR escuta CRM" desde o dia 1, alinhando com o ADR de longo prazo

---

## 2. Contexto

### 2.1. Estado anterior (até 2026-06-17 manhã)

- **ADR CRM (2026-06-12)** define 5 fases evolutivas:
  - Fase 1: write-only + refs externas (atual)
  - Fase 2: refresh on re-engagement
  - Fase 3: CRM interno mínimo (tabelas Contact/Deal/Org)
  - Fase 4: sync bidirecional (webhooks CRM → PeSDR)
  - Fase 5: organizations + multi-stakeholder

- **Spec PR #20** (mergeada 2026-06-17) propunha pra Fase 1:
  - `FormProviderAdapter` ABC + `RespondiFormAdapter` impl
  - Webhook `/webhooks/{slug}/form/{provider}` recebendo direto do Respondi
  - `tenant.yaml > forms.respondi.field_mapping` com `question_id → field`
  - Worker `process_form_inbound` cria Lead/Talk a partir do payload Respondi

### 2.2. Problema identificado durante implementação

Ao começar a implementação, identificamos 2 issues conceituais:

1. **Risco de duplicação de dados em produção real:**
   - Manoela usa RD Station como CRM operacional
   - Se Respondi ativar a integração nativa, contacts já vão pro RD Station automaticamente
   - PeSDR receberia o lead via webhook DIRETO do Respondi (caminho A)
   - PeSDR receberia o MESMO lead via webhook do RD Station depois (caminho B, futuro/Fase 4)
   - Conflict resolution + dedup virariam custo recorrente

2. **Hard code frágil de `question_id`:**
   - Spec original mapeia `qst_abc123 → nome` no tenant.yaml
   - Se Manoela editar o form (renomear pergunta, mudar ordem, adicionar campo), o `question_id` pode mudar
   - Manter sincronia entre painel Respondi e tenant.yaml é fricção operacional

### 2.3. Descoberta que muda o jogo (2026-06-17)

Pedro abriu o painel Respondi → **Integrações → CRMs** e confirmou:

> **RD Station CRM** (badge: PRO)
> *Envie novos contatos automaticamente para o RD Station CRM*
> [ Conectar ]

Integração nativa, sem necessidade de Zapier/Make/Pluga intermediário. Respondi alimenta RD Station diretamente. Isso muda completamente o cálculo: ao invés de PeSDR competir com a integração nativa, podemos **delegar a captura pra ela** e escutar o RD Station.

---

## 3. Decisão

**Na Fase 1 do ADR CRM, o ponto de entrada de leads no PeSDR é via webhook do CRM externo (RD Station), não via webhook do formulário (Respondi).**

```
ANTES (spec PR #20):
  Respondi ──webhook PeSDR──▶ Lead/Talk criados ─...─▶ CRM externo (escrita via action)

AGORA:
  Respondi ──integração nativa──▶ RD Station CRM ──webhook PeSDR──▶ Lead/Talk criados
                                                                          │
                                                                          └─...─▶ atualiza
                                                                                  mesmo card
                                                                                  via action
```

**Em palavras:**

- **Respondi** é a superfície de captura de leads (configurada pela Manoela)
- **RD Station CRM** recebe o contact via integração nativa do Respondi
- **PeSDR** escuta o webhook do RD Station (`contact_created` filtrado por origem/pipeline)
- **PeSDR** cria Lead + Talk e dispara mensagem proativa via WhatsApp
- **PeSDR** atualiza o **mesmo** contact/deal no RD Station via API conforme a conversação avança (parte da Fase B já planejada)

**O CRM externo (RD Station) atua como source of truth TEMPORÁRIO** enquanto o CRM interno da Fase 3 não existe.

---

## 4. Justificativa

| # | Razão | Impacto |
|---|---|---|
| **4.1** | **Zero hard code de question_id no tenant.yaml.** Mapping de campos vira responsabilidade do painel Respondi (que mapeia question → campo do CRM nativamente). | Manoela edita o form livremente sem PR |
| **4.2** | **Zero duplicação de dados.** RD Station é a única superfície de criação de contact/deal. PeSDR é consumidor + ator que atualiza o mesmo card. | Sem conflict resolution, sem dedup |
| **4.3** | **Antecipa o pattern "PeSDR escuta CRM"** previsto no ADR pra Fase 4 (bidirecional). Webhook handler do RD Station vira contract estável que cresce com o sistema. | Refactor pra Fase 3 fica menor |
| **4.4** | **Elimina categoria de adapter (`FormProviderAdapter`)** que se tornaria órfã na Fase 3 (quando sync engine intermedia entrada). | Menos código pra manter |
| **4.5** | **Mantém Fase B do plano original intacta** (`CRMActionAdapter` + `RDStationCRMBackend` pra saída). Caminho de escrita não muda. | Trabalho da Fase B é reaproveitado 100% |
| **4.6** | **Latência aceitável.** Lead → Respondi → RD Station → PeSDR → HSM em ~30s-2min é tolerável pra janela WhatsApp de 24h da Meta. | Lead não percebe diferença material |

---

## 5. Compatibilidade com o ADR CRM (2026-06-12)

**Esta decisão NÃO conflita com o ADR macro.** O ADR diz literalmente:

> *"O agente e console HITL conversam SEMPRE e SOMENTE com o CRM interno; só o sync engine fala com CRMs externos."*

Análise:

| Ponto do ADR | Como esta decisão respeita |
|---|---|
| "Agente conversa SEMPRE com CRM interno" | ✅ Hoje "CRM interno" = CRM externo (proxy temporário). Agente nunca toca o Respondi diretamente — só o RD Station. Quando Fase 3 chegar, agente passa a falar com CRM interno local — webhook do RD continua entrando, mas via sync engine. |
| "Só sync engine fala com CRMs externos" | ✅ Hoje não há sync engine ainda. O `CRMInboundAdapter` (entrada) e `CRMActionAdapter` (saída) são os predecessores dele. Quando Fase 3 chegar, ambos serão **encapsulados** pelo sync engine como subcomponentes, sem reescrita. |
| "Canônico nasce como modelo de domínio" | ✅ `ContactCanonical`, `DealCanonical` continuam como estão (Fase B da spec original). Vocabulário PeSDR independente de vendor mantém-se. |
| "4 decisões de não-bloqueio" | ✅ Todas preservadas: IDs internos próprios (`Lead.id` = UUID nosso), canônico de domínio, escrita via camada interna, audit trail (`action_executions`). |
| "Cliente sem CRM próprio?" | ⚠️ Cliente sem CRM **não vai funcionar** nesta arquitetura. Não é o caso da Manoela. Quando aparecer cliente sem CRM, `FormProviderAdapter` ressuscita como alternativa, ou criamos "CRM interno mínimo" antecipado pra esse cliente. ADR já aborda em §"Cliente sem CRM próprio" + Fase 3. |

---

## 6. Diagrama: Hoje (Fase 1) vs Futuro (Fase 3)

### 6.1. Hoje (Fase 1 com esta decisão)

```
┌────────────────────────────────────────────────────────────────┐
│                          EXTERNO                                │
│                                                                 │
│   ┌──────────┐     integração nativa     ┌────────────────┐    │
│   │ Respondi │─────(configurada pela────▶│ RD Station CRM │    │
│   │ (form)   │      Manoela no painel)   │ (source of     │    │
│   └──────────┘                            │  truth temp)   │    │
│                                           └───────┬────────┘    │
└───────────────────────────────────────────────────┼─────────────┘
                                                    │ webhook contact_created
                                                    │ (filtrado por origem)
                                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                          PESDR                                  │
│                                                                 │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  POST /webhooks/{slug}/crm/{provider}                │     │
│   │  RDStationCRMInboundAdapter                          │     │
│   │  ├─ valida HMAC/secret                               │     │
│   │  ├─ filtra: criado via integração nativa?            │     │
│   │  └─ normaliza → IngestedCRMEvent                     │     │
│   └──────────────────────┬───────────────────────────────┘     │
│                          │                                      │
│                          ▼                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  find_or_create_lead_by_crm_event                    │     │
│   │  cria Talk + TalkFlowState pré-populado              │     │
│   └──────────────────────┬───────────────────────────────┘     │
│                          │                                      │
│                          ▼                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  Worker job: process_crm_event                       │     │
│   │  └─ send HSM proativo                                │     │
│   └──────────────────────────────────────────────────────┘     │
│                                                                 │
│   ───── (lead responde no WhatsApp) ─────                       │
│                                                                 │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  run_turn → on_collected → CRMActionAdapter          │     │
│   │  → RDStationCRMBackend.create_or_update_deal()       │     │
│   │  (atualiza o MESMO contact_id criado pelo Respondi)  │     │
│   └────────────────────────┬─────────────────────────────┘     │
└────────────────────────────┼────────────────────────────────────┘
                             │ API write
                             ▼
                       (atualiza card no RD Station)
```

### 6.2. Futuro (Fase 3 quando entrar — CRM interno operacional)

```
┌────────────────────────────────────────────────────────────────┐
│                          EXTERNO                                │
│   ┌──────────┐                            ┌────────────────┐   │
│   │ Respondi │───integração nativa───────▶│ RD Station CRM │   │
│   └──────────┘                            └───────┬────────┘   │
└───────────────────────────────────────────────────┼─────────────┘
                                                    │ webhook
                                                    ▼
┌────────────────────────────────────────────────────────────────┐
│                          PESDR                                  │
│                                                                 │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  RDStationCRMInboundAdapter (MESMO CÓDIGO)           │     │
│   │  retorna IngestedCRMEvent                            │     │
│   └──────────────────────┬───────────────────────────────┘     │
│                          │                                      │
│                          ▼                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  SYNC ENGINE (NOVO na Fase 4)                        │     │
│   │  ├─ reconcile com CRM interno (Fase 3 — tabelas)     │     │
│   │  ├─ resolve_conflict (por campo, com dono declarado) │     │
│   │  └─ decide: criar Talk? atualizar? ignorar?          │     │
│   └──────────────────────┬───────────────────────────────┘     │
│                          │                                      │
│                          ▼                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  CRM INTERNO (Fase 3 — Contact/Deal/Org tables)      │     │
│   │  Source of truth definitivo                          │     │
│   └──────────────────────┬───────────────────────────────┘     │
│                          │                                      │
│                          ▼                                      │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  cria Talk pré-populado a partir do CRM interno      │     │
│   │  (não mais direto do CRMInboundAdapter)              │     │
│   └──────────────────────────────────────────────────────┘     │
│                                                                 │
│   ───── (lead responde no WhatsApp) ─────                       │
│                                                                 │
│   ┌──────────────────────────────────────────────────────┐     │
│   │  on_collected → CRMActionAdapter (MESMO CÓDIGO)      │     │
│   │  agora escreve LOCAL primeiro, depois sync engine    │     │
│   │  propaga pra CRM externo                             │     │
│   └──────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────┘
```

**Mudança chave entre Hoje e Fase 3:** `find_or_create_lead_by_crm_event` (Fase 1) vira `sync_engine.reconcile(event)` (Fase 3). Tudo ao redor permanece — o webhook continua chegando, o adapter de entrada permanece, o action adapter de saída permanece.

---

## 7. Contracts estáveis pra refactor limpo na Fase 3

Pra evitar débito técnico, **3 contratos** precisam ser respeitados desde já:

### 7.1. `IngestedCRMEvent` é o output normalizado do `CRMInboundAdapter`

```python
@dataclass(frozen=True)
class IngestedCRMEvent:
    """Evento CRM normalizado — independente de vendor."""

    external_id: str
    """ID do evento no CRM externo (idempotency key)."""

    event_type: Literal[
        "contact_created",
        "contact_updated",
        "deal_created",
        "deal_stage_changed",
        # ... outros eventos relevantes
    ]

    contact_external_id: str | None
    """ID do contact no CRM externo (referência)."""

    deal_external_id: str | None
    """ID do deal no CRM externo (se aplicável)."""

    lead_identifier: LeadIdentifier
    """phone E.164 normalizado + email opcional."""

    field_values: dict[str, Any]
    """Campos populados no CRM (do form ou edição manual)."""

    source_meta: dict[str, Any]
    """Origin do evento: 'integration' | 'manual' | UTMs | tags."""

    occurred_at_iso: str
    """Timestamp do evento no CRM externo."""
```

**Por que estável:** Sync engine da Fase 4 vai consumir EXATAMENTE esse shape. A reconciliação interna usa `contact_external_id` pra fazer lookup. Sem mudança no contract.

### 7.2. Worker job `process_crm_event` deve ser fino e substituível

```python
# Hoje (Fase 1):
async def process_crm_event(ctx, event_id):
    event = await load_event(event_id)

    # Fase 1: cria Talk direto a partir do CRM externo
    lead = await find_or_create_lead_by_crm_event(event)
    talk = await create_talk_from_crm_event(lead, event)
    await send_proactive_hsm(talk)

# Fase 3+ (refactor de 1 PR):
async def process_crm_event(ctx, event_id):
    event = await load_event(event_id)

    # Fase 3: sync engine reconcilia primeiro
    internal_record = await sync_engine.reconcile(event)

    # Decisão de criar Talk fica no sync engine
    if internal_record.should_open_talk:
        talk = await create_talk_from_internal(internal_record)
        await send_proactive_hsm(talk)
```

**Por que estável:** route handler do webhook não muda. Adapter não muda. Apenas a lógica interna do worker job muda — escopo localizado.

### 7.3. `Lead.crm_refs` JSONB continua como source de identidade externa

```python
# Estrutura no Fase 1 (atual):
lead.crm_refs = {
    "rdstation": {
        "contact_id": "abc123",
        "deal_id_mentoria": "def456",
        "synced_via": "inbound_webhook"  # NOVO — origem da criação
    }
}

# Fase 3 (após refactor):
# Mesma estrutura. Sync engine usa lookup local + Lead.crm_refs.
# Quando sync engine criar/atualizar contact no externo, atualiza refs igual.
```

**Por que estável:** schema JSONB é flexível. Adicionar campo `synced_via` agora não custa nada e dá rastro de migração pra Fase 3.

---

## 8. Plano de migração pra Fase 3 (visão macro)

Quando Fase 3 entrar (gatilho ADR: 2-3 clientes OR dor de sync no piloto):

| Passo | Escopo | Esforço estimado |
|---|---|---|
| 1. Criar tabelas Contact, Deal, Organization (migration nova) | Schema + ORM + repositórios | ~3-5 dias |
| 2. Implementar `sync_engine.reconcile(IngestedCRMEvent)` | Lógica de matching + conflict resolution | ~5-7 dias |
| 3. Refatorar `process_crm_event` worker pra chamar sync engine | 1 PR cirúrgico | ~1 dia |
| 4. Refatorar `CRMActionAdapter` pra escrever local-first + sync | Modificar handlers do backend | ~2-3 dias |
| 5. Migrar dados: `Lead.crm_refs` → rows em Contact/Deal | Script de migração one-shot | ~1 dia |
| 6. Console HITL evolui pra mostrar Contact/Deal local | UI changes | ~3-5 dias |

**Pontos não afetados:**
- `RDStationCRMInboundAdapter` (caminho de entrada)
- Webhook route `/webhooks/{slug}/crm/{provider}`
- `RDStationCRMBackend` HTTP client (continua chamando RD Station API)
- TreeFlow YAML schema (`on_collected: adapter: crm`)
- Tenant.yaml `crm` block

---

## 9. O que esta decisão REVOGA da spec PR #20

Da spec mergeada em PR #20, **revogam-se** os seguintes itens da Fase A (Form ingestion):

- ❌ `FormProviderAdapter` ABC + `IngestedFormSubmission` dataclass + `LeadIdentifier`-via-form
- ❌ `forms/` subsistema (`base.py`, `registry.py`, `factory.py`, `errors.py`, `ingest.py`, `respondi.py`)
- ❌ Route `POST /webhooks/{slug}/form/{provider}`
- ❌ Worker job `worker/jobs/forms.py` (process_form_inbound)
- ❌ Migration `inbound_form_submissions` table
- ❌ Model `InboundFormSubmission` + repository
- ❌ Schema `FormProviderConfig` no tenant_yaml.py
- ❌ `tenant.yaml > forms.respondi.field_mapping`

Tarefas A1–A10 da Fase A originais ficam **substituídas** pela nova Fase A revisada (§10 abaixo).

## 10. O que esta decisão PRESERVA da spec PR #20

Tudo da **Fase B** (CRM write-only via ActionAdapter):

- ✅ `CRMActionAdapter` (registrado como `name="crm"` no FE-03c registry)
- ✅ `CRMBackend` ABC + registry + factory
- ✅ `RDStationCRMBackend` com OAuth + handlers canônicos
- ✅ `ContactCanonical`, `DealCanonical`, `DealStage` (vocabulário interno)
- ✅ `Lead.crm_refs` JSONB (migration 0030)
- ✅ Schema `CRMConfig`, `RDStationCRMConfig` no tenant_yaml.py
- ✅ `tenant.yaml > crm.rdstation` block
- ✅ TreeFlow `on_collected: adapter: crm` na Manoela
- ✅ Templating Jinja2 com `lead.crm_refs` no contexto
- ✅ As 4 decisões de não-bloqueio do ADR CRM

E as **decisões do Nicolas nas 12 open questions** da PR #20 (comentário de 2026-06-17), salvo as que dependiam da existência do `FormProviderAdapter`:

- ✅ #1 Localização CRM em `flowengine/actions/crm/`
- ✅ #2 Pré-popular `state.collected['__prefilled_fields__']` (vale pro CRM event também)
- ✅ #3 `lead.crm_refs` no template context
- ✅ #4 `crm.provider` ausente = skip + warning
- ⏸️ #5 OAuth refresh rotation (depende RD Station — ainda em aberto)
- ✅ #6 Worker em arquivo separado (`worker/jobs/crm_inbound.py` em vez de `forms.py`)
- ✅ #7 Multi-form num tenant → vira "multi-CRM-provider num tenant" (mesma filosofia de hedge)
- ✅ #8 HSM proactive `proactive_template_unapproved` em `RequiresReviewReason`
- ⚠️ #9 Identity Resolver / `normalize_e164` — **ainda vale**, agora compartilhado entre WhatsApp e CRM webhook (em vez de WhatsApp e form)
- ✅ #10 Sintaxe TreeFlow YAML
- ✅ #11 Versão Manoela v0.3.0
- ⏸️ #12 Smoke (depende sandbox RD Station — ainda em aberto)

---

## 11. Nova Fase A (substituindo a antiga "Form ingestion")

### 11.1. Renomeação

| Antes | Depois |
|---|---|
| **Fase A** — Form ingestion (Respondi) | **Fase A** — CRM inbound (RD Station webhook) |

### 11.2. Tasks da nova Fase A (preliminar — virá em spec amendment formal)

| Task | Escopo | Estimativa |
|---|---|---|
| **A1** | Migration: `Lead.crm_refs JSONB` + `inbound_crm_events` table (substitui `inbound_form_submissions`) | S |
| **A2** | Schema tenant.yaml — `CRMInboundConfig` per-provider (sem `FormProviderConfig`) | S |
| **A3** | Model + Repository `InboundCRMEvent` | S |
| **A4** | Subsistema `crm/inbound/`: ABC + registry + factory + errors | M |
| **A5** | `RDStationCRMInboundAdapter` (HMAC verify do RD Station, parsing, filtro por origin) | M |
| **A6** | Helper `_match_lead()` compartilhado entre WhatsApp ingest e CRM event ingest + `normalize_e164()` | M |
| **A7** | Route `POST /webhooks/{slug}/crm/{provider}` em `api/routes/crm_inbound.py` | S |
| **A8** | Worker job `process_crm_event` em `worker/jobs/crm_inbound.py` | M |
| **A9** | Logic de pré-popular `state.collected['__prefilled_fields__']` (vale pro CRM event) | S |
| **A10** | Tests unit + integration + e2e fixture (payload RD Station real) | M |
| **A11** | CLAUDE.md ganha seção "CRM inbound (Plano 7a)" | S |

**Fase B inalterada** (12 tasks da spec original — CRM RD Station write-only).

**Fase C renomeada**: "Wiring na Manoela" mantém-se, mas o `tenant.yaml` ganha bloco `crm.inbound:` em vez de `forms.respondi:`.

---

## 12. Configurações fora do código (Manoela)

Manoela / Lana fazem (uma vez):

1. **No painel Respondi:** clica em "Conectar" → integração com RD Station CRM (PRO). Autoriza conta da Manoela.
2. **No painel Respondi:** configura "Para quais forms enviar?" → Mentoria Icônica (`QWHmKbnx`) por enquanto. Aceleradora entra depois.
3. **No painel RD Station:**
   - Cria pipeline pra Manoela (anota `pipeline_id`)
   - Define 3 stages (anota `stage_id` × 3 — open / won / lost)
   - Cria custom fields necessários (anota `custom_field_id` × N)
   - Cria app OAuth (anota `client_id`, `client_secret`)
   - **Configura webhook**: `POST https://sdr.luminai.ia.br/webhooks/manoela-mentora/crm/rdstation` com secret HMAC compartilhado
   - Eventos a escutar: `contact_created` (mínimo). Pode adicionar `deal_created` e `deal_stage_changed` depois.

---

## 13. Anti-débitos identificados e mitigações

| Anti-débito | Mitigação |
|---|---|
| RD Station fora do ar = lead perdido | Monitoring + alert se webhook não chega há > X minutos. RD Station tem SLA decente. |
| `IngestedCRMEvent` fica obsoleto na Fase 3 | Já está desenhado pra ser consumido pelo sync engine. Nenhuma mudança esperada. |
| Detecção de "origin: integration" pode não funcionar (RD não documenta) | Fallback: se origem não vier no payload, filtrar por presença de tag/UTM específica da integração Respondi. Anti-débito: documentar explicitamente o critério no `RDStationCRMInboundAdapter`. |
| Lead criado manualmente pela Lana no painel RD vira Talk? | **Não.** Filtro de origem garante: só evento com `source: "integration"` (ou equivalente) abre Talk. Manual = ignorar. |
| Identity match errado entre Lead RD e Lead WhatsApp já existente | `_match_lead()` compartilhado (decisão #9 Nicolas) — phone E.164 normalizado é primary key. |
| Atualização do MESMO card (escrita) com dados conflitantes | Idempotência multi-camada do FE-03c (UNIQUE talk+field+value_hash) + lookup de `Lead.crm_refs.rdstation.contact_id` antes de criar. |

---

## 13.bis. Ajustes do review do Nicolas (2026-06-23)

Após review da spec, Nicolas aprovou a direção com 1 ajuste obrigatório + 1 recomendação:

### 13.bis.1. Gate de produção obrigatório

A spec original classificava OQ-A1 (origin filter) como "não-bloqueador da Fase A". Nicolas concorda que não bloqueia *implementar*, mas **bloqueia LIGAR EM PRODUÇÃO**:

> Sem o `origin_filter`, qualquer contato criado à mão no RD Station (pela Lana, import em massa, ou o próprio vendedor) dispara Talk + HSM proativo pra um lead que nunca pediu contato. Isso é um incidente de UX/LGPD esperando acontecer.

🔴 **Gate explícito:** O `crm.inbound.rdstation.enabled: true` no `tenant.yaml` de PRODUÇÃO fica bloqueado até OQ-A1 (origin filter) E OQ-A2 (auth) estarem resolvidas e validadas com payload real.

Em código:
- Implementação da Fase A pode ir até o fim com `RDStationCRMInboundAdapter` funcional
- `tenant.yaml` da Manoela mantém `crm.inbound.rdstation.enabled: false` até as 2 OQs serem resolvidas
- Validator no `tenant_loader` emite warning se `enabled: true` sem `origin_filter` configurado (advisory, não erro)

### 13.bis.2. Confirmar OQ-A2 antes de implementar A8

O `CRMInboundProviderConfig` originalmente exigia `hmac_secret_ref` obrigatório. RD Station pode não suportar HMAC nativo. Antes de implementar `_validate_signature`, Pedro confirma:
- HMAC → mantém `hmac_secret_ref` obrigatório
- URL secret → ajusta contract: `hmac_secret_ref` opcional + `url_secret_ref` opcional, validator exige um dos dois

### 13.bis.3. Observability emit (recomendação Nicolas — barata, evita falha silenciosa)

`RDStationCRMInboundAdapter` emit structlog event toda vez que filtrar evento por origem:

```python
log.info(
    "crm.event.filtered_origin",
    tenant=tenant.slug, provider="rdstation",
    event_id=payload.event_id,
    source_meta_raw=payload.source_meta,   # payload bruto pra depurar
    accept_sources_configured=origin_filter.accept_sources,
    decision="ignored",
)
```

Operador inspeciona `logs/crm.event.filtered_origin` quando "lead chegou no RD mas não veio mensagem" — sem precisar abrir DB.

### 13.bis.4. Não-bloqueantes adicionais

- **OQ-A7 (novo):** `synced_via: "inbound_webhook"` basta pro sync engine Fase 3 distinguir, ou precisa de mais campo?
- **OQ-A8 (novo):** Validator pra avisar quando `crm.provider` (saída) ≠ `crm.inbound.<provider>` (entrada)?

### 13.bis.5. Nota separada do Nicolas

> Como esse fluxo adiciona um 3º caminho de find-or-create de lead, reforça que Identity Resolver / Plano 6 deveria vir junto/antes do CRM — mas não bloqueia esta spec pro piloto Manoela.

---

## 14. Open questions desta decisão (não da spec original)

| # | Pergunta | Status | Quando resolver |
|---|---|---|---|
| **14.1 / OQ-A1** | RD Station marca origem ("via integração" vs "manual") no payload? | 🔴 **GATE DE PRODUÇÃO** | Pedro investiga ao configurar webhook |
| **14.2 / OQ-A2** | HMAC ou URL secret nos webhooks RD? | 🟠 **Bloqueia A8** | Pedro confirma antes de implementar A8 |
| **14.3 / OQ-A3** | Retry policy do webhook falhado? | 🟡 Não-bloqueador | Pedro confirma na doc |
| **14.4 / OQ-A4** | Múltiplos forms Respondi distinguíveis no contact RD? | 🟡 Não-bloqueador | Antes de Aceleradora |
| **14.5 / OQ-A5** | RD Station tem sandbox? | 🟡 Não-bloqueador | Antes de Fase C |
| **14.6 / OQ-A6** | `refresh_token` rotaciona? | 🟡 Não-bloqueador | Antes de Fase B |
| **14.7 / OQ-A7** | `synced_via: "inbound_webhook"` basta pro sync engine Fase 3? | 🟢 Diferido | Quando Fase 3 entrar |
| **14.8 / OQ-A8** | Validator pra provider mismatch? | 🟢 Diferido | Quando entrar 2º vendor |

---

## 15. Relação com outras specs/ADRs

- **ADR CRM (2026-06-12)** — base. Esta nota implementa Fase 1 com nuance.
- **Adapter Pattern (2026-05-24)** — adiciona 5ª borda: `CRMInboundAdapter` (entrada CRM). Equilibra com `CRMActionAdapter` (saída) e segue mesmo pattern de ABC + registry + factory.
- **FE-03c (2026-06-12)** — reusa framework de actions intacto pra saída.
- **Spec Form Ingestion PR #20 (2026-06-16)** — esta nota é o **amendment técnico** da Fase A. Fase B e C preservadas.

---

## 16. Próximos passos

1. **Nicolas revisa** esta nota + spec amendment (próximo doc) + diff do CLAUDE.md
2. **Nicolas aprova ou pede ajustes**
3. **Quando aprovado**, escrevemos plan executável via skill `writing-plans` pra nova Fase A (11 tasks revisadas)
4. **Pedro configura no Respondi** (clica em "Conectar" RD Station)
5. **Pedro configura no RD Station** (webhook URL, OAuth app, pipeline, stages, custom fields)
6. **Pedro responde** as 6 open questions desta nota (#14.1 a #14.6) com base na configuração real
7. **Implementação começa**: Fase A revisada → Fase B (preservada) → Fase C (wiring)

---

**Fim da Decision Note.**
