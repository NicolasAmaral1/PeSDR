# Form ingestion + CRM write-only (RD Station) — Design Spec

**Data:** 2026-06-16
**Status:** Draft pra revisão e alinhamento (Nicolas + Pedro)
**Tipo:** Architectural design — proposta de implementação, ainda não plan executável
**Autor:** Pedro Aranda (auxiliado por Claude Code, com lente do estado atual do FlowEngine v2)
**Evolui:**
- [`2026-06-12-crm-posture-decision.md`](./2026-06-12-crm-posture-decision.md) — ADR macro do CRM (esta spec implementa a **Fase 1: write-only + refs**)
- [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) — pattern macro de adapters em bordas
**Reutiliza:**
- [`2026-06-12-fe03c-actions-adapter-framework-design.md`](./2026-06-12-fe03c-actions-adapter-framework-design.md) — Framework de actions é a base do CRM out
- [`2026-05-24-messaging-adapter-design.md`](./2026-05-24-messaging-adapter-design.md) — Pattern de referência pro FormProviderAdapter
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) §7 (entrada de lead), §8 (sync CRM)
**Informa:** Plano 7 (CRM), planos futuros de multi-form e multi-CRM

---

## TL;DR

PeSDR precisa **receber leads de formulários** (Respondi pro piloto Manoela) e **escrever no CRM externo** (RD Station pro piloto). Esta spec propõe uma implementação que respeita 3 decisões já tomadas:

1. **ADR CRM (2026-06-12):** PeSDR vai ter CRM interno operacional + sync bidirecional via adapters. Esta spec implementa a **Fase 1** (write-only com refs externas — sem tabelas Contact/Deal/Org ainda, mas com as 4 decisões de não-bloqueio embutidas pra Fase 3+ futura não exigir refactor).
2. **FE-03c Actions Framework:** o framework de `on_collected` + `ActionAdapter` já entrega TODA a infra de side-effects assíncronos com idempotência e retry. **CRM out = ActionAdapter genérico `crm` + backends por vendor**, zero código novo de framework.
3. **Adapter pattern em bordas (ADR 2026-05-24):** 4ª borda agora é **FormProvider** (entrada de lead via formulário) — categoria nova, paralela ao MessagingAdapter. NÃO é ActionAdapter (que é saída) — semanticamente diferente.

**Resultado proposto:** 1 novo subsistema (`forms/`), 1 novo adapter genérico de CRM (`crm` no registry de actions com backends plugáveis por vendor), 1 nova coluna em `leads` (`crm_refs JSONB`), 1 novo job de worker (`process_form_inbound`), 2 novas seções em `tenant.yaml`. Sem novas tabelas de domínio (Contact/Deal/Org ficam pra Fase 3 do ADR). Escalável: novo CRM = novo backend (~150 LOC); novo formulário = novo FormProviderAdapter (~120 LOC).

**Plano executável:** §8 detalha **estrutura final de pastas**, **code stubs dos 8 contracts principais**, **27 tarefas numeradas** (10 da Fase A + 12 da Fase B + 5 da Fase C) com critérios de aceitação, e **3 PRs separados** sugeridos pra revisão incremental do Nicolas. Total estimado: ~3.5 semanas de dev focado, ~38 arquivos novos, ~3500 LOC.

---

## Índice

1. [Contexto e motivação](#1-contexto-e-motivação)
2. [Decisões arquiteturais](#2-decisões-arquiteturais)
3. [Modelagem: Form ingestion (entrada)](#3-modelagem-form-ingestion-entrada)
4. [Modelagem: CRM write-only (saída)](#4-modelagem-crm-write-only-saída)
5. [Mudanças de schema](#5-mudanças-de-schema)
6. [Fluxo end-to-end (cenário Manoela)](#6-fluxo-end-to-end-cenário-manoela)
7. [Escalabilidade — multi-form e multi-CRM](#7-escalabilidade--multi-form-e-multi-crm)
8. [Implementação fasada](#8-implementação-fasada)
9. [Trade-offs explícitos](#9-trade-offs-explícitos)
10. [Riscos e mitigações](#10-riscos-e-mitigações)
11. [Open questions pro Nicolas](#11-open-questions-pro-nicolas)
12. [Não-objetivos](#12-não-objetivos-fora-de-escopo)

---

## 1. Contexto e motivação

### 1.1. Cenário do piloto (Manoela)

Manoela Mentora capta leads via **formulário Respondi** (pergunta nome, telefone, faturamento estimado, momento profissional). Hoje os leads caem direto no funil de WhatsApp dela manual. Queremos que o PeSDR:

1. Receba o lead do Respondi via webhook
2. Dispare automaticamente a primeira mensagem proativa via WhatsApp (template HSM, já implementado em Plano 9)
3. Conduza a qualificação via FlowEngine v2 (TreeFlow `qualificacao_inicial.yaml` v0.2.1, já existente)
4. À medida que campos são coletados (`nome`, `faturamento_mensal`), crie/atualize **contato + deal no RD Station CRM** dela
5. Permita a operadora (Lana, estrategista) acompanhar pelo console HITL

### 1.2. Por que agora

| Vetor | Status |
|---|---|
| Manoela ativando piloto em produção | Bloqueada por falta de form ingestion + CRM out |
| ADR CRM (Fase 1) prometida pra "quando CRM do cliente for conhecido" | É agora — RD Station é o CRM da Manoela |
| FE-03c Actions Framework recém-mergeado | Pronto pra ser exercitado com o primeiro adapter real (até agora só `LoggingActionAdapter` fake) |
| Próximos clientes (após Manoela) usam outros CRMs / outros forms | Arquitetura precisa nascer multi-vendor |

### 1.3. Restrições de design

- **Respeitar FE-03c.** Não criar segundo framework de side-effects paralelo.
- **Respeitar ADR CRM Fase 1.** Não criar tabelas Contact/Deal/Org ainda — refs externas em `Lead.crm_refs` JSONB, mas com shape canônico do ADR (`stage: open|won|lost`, etc).
- **Respeitar pattern de adapter em bordas (ADR 2026-05-24).** Form é nova borda, com seu próprio ABC + factory + registry — espelha `MessagingAdapter` em estrutura.
- **Multi-tenant desde o dia 1.** Tudo passa por `tenant.yaml` config + RLS.
- **Outros clientes virão.** Não acoplar a Manoela/Respondi/RDStation; eles são os primeiros impls.

### 1.4. Mapeamento conceitual: as 4 bordas pós-FE-03c + esta spec

| Borda | Direção | ABC | Default impl (standalone) | Pattern |
|---|---|---|---|---|
| **Messaging** | I/O (recebe + envia mensagens do lead) | `MessagingAdapter` | `WhatsAppCloudAdapter` | Existe (Plano 5) |
| **Identity** | resolve lead → contact estável | `IdentityResolver` | `InternalLead` | Plano 6 (futuro) |
| **HITL** | escalation pra operador | `HitlSink` | Console Plano 11 + Vialum futuro | Existe |
| **Action (CRM/Calendar/etc)** | output assíncrono em sistema externo | `ActionAdapter` | `LoggingActionAdapter` (fake) | Existe (FE-03c) |
| **Form (NOVO)** | input de lead via formulário | `FormProviderAdapter` | `RespondiFormAdapter` | **Proposto aqui** |

Form **não é** ActionAdapter — semântica é oposta:

- ActionAdapter: PeSDR → sistema externo (saída disparada por `on_collected`)
- FormProviderAdapter: sistema externo → PeSDR (entrada via webhook, inicia Talk)

Misturar quebra a contract dos dois. Manter separado preserva clareza de fluxo.

---

## 2. Decisões arquiteturais

### 2.1. Decisão 1 — CRM out via ActionAdapter genérico `crm` + backends

**Decisão:** Criar **um único** `CRMActionAdapter` registrado no FE-03c registry com `name = "crm"`. Ele lê `tenant.yaml > crm.provider` em runtime e despacha pro backend correto.

```python
# pseudo-código
@register
class CRMActionAdapter(ActionAdapter):
    name = "crm"

    def __init__(self, tenant_config, secrets):
        super().__init__(tenant_config, secrets)
        provider = tenant_config.crm.provider  # "rdstation" | "hubspot" | ...
        self.backend = build_crm_backend(provider, tenant_config, secrets)

    async def execute(self, *, handler, params):
        return await self.backend.execute(handler=handler, params=params)
```

E backends por vendor (`RDStationCRMBackend`, `HubSpotCRMBackend`, ...) com interface comum.

**Por que UM adapter + backends, NÃO N adapters (`rdstation`, `hubspot`)?**

| Aspecto | UM adapter + backends (escolha) | N adapters por vendor |
|---|---|---|
| TreeFlow YAML | `adapter: crm` (vendor-agnostic) | `adapter: rdstation` (vendor-locked) |
| Trocar CRM no tenant | só muda `tenant.yaml > crm.provider` | tem que reescrever TODO TreeFlow YAML |
| Handlers padronizados | sim (contract por interface) | não (cada adapter livre) |
| Compliance com ADR ("canônico nasce como modelo de domínio") | ✅ shape único | ❌ acoplado a vendor |
| Custo de adicionar CRM novo | 1 backend (~150 LOC) | 1 adapter + reescrever YAMLs dos tenants |

**Handlers padronizados (vocabulário do canônico do ADR):**

| Handler | Semântica | Idempotência |
|---|---|---|
| `create_or_update_contact` | upsert contact por (email, phone, external_id) | natural (lookup → create/update) |
| `create_or_update_deal` | upsert deal por (lead_id, product) | natural |
| `update_deal_stage` | muda stage (open → won/lost) | natural (idempotente por valor) |
| `attach_contact_to_deal` | vincula contato ao deal | natural |
| `record_qualification_note` | append nota ao contato | requer dedup por value_hash do FE-03c |

Cada backend implementa esses handlers chamando a API do vendor.

### 2.2. Decisão 2 — Form ingestion via novo subsistema `forms/`

**Decisão:** Criar `src/ai_sdr/forms/` com a mesma estrutura de `src/ai_sdr/messaging/`:

```
forms/
├── __init__.py            # side-effect import dos adapters
├── base.py                # FormProviderAdapter ABC + IngestedFormSubmission
├── registry.py            # FORM_PROVIDERS dict + @register decorator
├── factory.py             # build_form_adapter(name, tenant, secrets)
├── errors.py              # SignatureError, MalformedPayload, etc
├── ingest.py              # ingest_form_submission helper (find-or-create Lead, create Talk, enqueue first turn)
└── respondi.py            # RespondiFormAdapter — primeiro impl
```

**Contract base:**

```python
@dataclass(frozen=True)
class IngestedFormSubmission:
    external_id: str          # provider-native id (dedup key)
    submitted_at_iso: str
    lead_identifier: LeadIdentifier  # phone E.164 + email opcional
    field_values: dict[str, Any]     # campos normalizados pro vocabulário PeSDR
    source_meta: dict[str, Any]      # form_id, utms, raw, etc — pra audit

class LeadIdentifier(BaseModel):
    whatsapp_e164: str | None
    email: str | None
    external_label: str | None
    # validator: pelo menos 1 deve ser não-None

class FormProviderAdapter(ABC):
    @abstractmethod
    async def handle_submission(
        self, raw_body: bytes, headers: Mapping[str, str], url_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        """Valida + parseia. Raise SignatureError se assinatura inválida."""
```

Por que **não** reusar `MessagingAdapter`: o contract é `handle_inbound() -> list[InboundMessage]`. Form não é mensagem (não tem `text`). Forçar virar mensagem perderia metadata (utms, score, etc) e acoplaria conceitos diferentes.

### 2.3. Decisão 3 — Pré-popular `collected` do TalkFlowState com campos do form

Quando o form entrega `nome`, `email`, `faturamento_mensal`, o `TalkFlowState` do Talk recém-criado já nasce com esses valores em `collected`. O FlowEngine v2 vê os campos como já preenchidos.

**Implicação no TreeFlow:**
- Se o node `saudacao` declara `collect: [nome]` e o form já trouxe `nome`, o `exit_condition: all_fields_filled` é satisfeito antes do primeiro turno — o node é "pulado" como se já tivesse rodado.
- O LLM no `saudacao` precisa saber disso pra cumprimentar pelo nome direto, em vez de perguntar. **Solução:** o system prompt do node tem que receber sinal de "campo pré-coletado via form" — ou o TreeFlow YAML tem nodes alternativos (`saudacao_with_name` quando `is_set('nome')`).

**Mapeamento via `tenant.yaml > forms.<provider>.field_mapping`:**

```yaml
forms:
  respondi:
    enabled: true
    shared_secret_ref: secrets/respondi_webhook_secret
    start_treeflow: qualificacao_inicial
    field_mapping:
      # form_question_id (do Respondi) → collected_field (do TreeFlow)
      qst_abc123: nome
      qst_def456: whatsapp_e164    # vai pra LeadIdentifier, não collected
      qst_ghi789: faturamento_mensal
    proactive_first_message:
      enabled: true
      template_ref: "saudacao_proativa_v1"   # HSM aprovado no Meta
      language: "pt_BR"
      params:
        - "{{ collected.nome | default('') }}"
```

Mapeamento explícito por tenant: a operadora controla como o form fala com o TreeFlow.

### 2.4. Decisão 4 — Primeira mensagem proativa via template HSM (Plano 9)

Lead chega via form → não está em janela de 24h do WhatsApp → não dá pra enviar mensagem livre. Tem que ser **HSM (template aprovado pela Meta)**.

Plano 9 já implementou `send_template` no `WhatsAppCloudAdapter`. Esta spec só **conecta**: o worker `process_form_inbound` chama `messaging_adapter.send_template(...)` usando o `proactive_first_message.template_ref` do tenant.yaml.

**Lifecycle do Talk após HSM enviado:**
- Talk nasce em `status='active'`
- Primeira mensagem agendada como `outbound` (vai pro audit `outbound_messages` que já existe — Plano 10)
- Quando lead responder → webhook `whatsapp_cloud` recebe → worker `process_lead_inbox` pega → `run_turn` normal

### 2.5. Decisão 5 — CRM Fase 1 (write-only) sem tabelas Contact/Deal/Org

ADR de CRM lista 5 fases evolutivas. Esta spec implementa **Fase 1** (write-only + refs) e **Fase 2** (refresh on re-engagement — opcional, dependendo do escopo aceito).

**Implementação Fase 1:**
- `Lead.crm_refs` JSONB armazena `{"rdstation": {"contact_id": "...", "deal_id": "...", "last_synced_at": "..."}}`
- `CRMActionAdapter.create_or_update_contact` retorna `external_id` → grava em `Lead.crm_refs.rdstation.contact_id`
- Idempotência: `action_executions` UNIQUE `(talk, field, value_hash)` (FE-03c) **+** backend verifica `Lead.crm_refs.rdstation.contact_id` antes de criar (se existe, faz update; se não, create)

**Por que NÃO criar tabelas Contact/Deal/Org agora:**
- ADR explicitamente puxa essas tabelas pra Fase 3 (depois de 2-3 clientes OR dor real de sync)
- Custo de 4 migrations + ORM + repositórios + UI HITL adaptada é grande
- Refs em JSONB cobrem 100% das necessidades do piloto Manoela (cenário 2 do ADR — upsell — é Fase 2)

**4 decisões de não-bloqueio do ADR aplicadas DESDE JÁ:**

| ADR decisão | Como aplicamos agora |
|---|---|
| 1. IDs internos próprios | `Lead.id` (UUID nosso) é a PK; RD Station id vira atributo em `crm_refs` |
| 2. Canônico nasce como modelo de domínio | Handler signatures usam vocabulário `Contact`, `Deal` (não `RDStationDeal`); shape `stage: open\|won\|lost` em todos os backends |
| 3. Toda escrita comercial passa por camada interna | CRM call só rola via `CRMActionAdapter.execute` → backend; nenhum outro caminho |
| 4. Audit trail desde cedo | `action_executions` já é o audit (FE-03c); contém `params_resolved` + `external_id` + timestamps |

Quando Fase 3 entrar, basta:
- Criar tabelas Contact/Deal/Org
- Migrar refs de `Lead.crm_refs` pra rows (script de migração)
- Atualizar backends pra escrever local primeiro, depois propagar via sync engine

Zero refactor de TreeFlow YAML, runtime, ou tenant.yaml.

---

## 3. Modelagem: Form ingestion (entrada)

### 3.1. Webhook route

```
POST https://<host>/webhooks/{tenant_slug}/form/{provider}

# Por exemplo, Manoela + Respondi:
POST https://sdr.luminai.ia.br/webhooks/manoela-mentora/form/respondi
```

URL pattern paralelo ao messaging (`/webhooks/{slug}/{provider}`), com path segment `form/` deixando explícita a categoria.

**Validações ordenadas (fail-fast):**

1. `tenant_slug` existe → 404 se não
2. `tenant.yaml > forms.<provider>.enabled = true` → 404 se não
3. `FormProviderAdapter` registrado pra `<provider>` → 404 se não (registry lookup)
4. `adapter.handle_submission(raw_body, headers, query_params)` → pode raise:
   - `SignatureError` → 401
   - `MalformedPayload` → 400
   - Outras → 500 (alerta)
5. Sucesso → 200 com body `{"status": "queued", "lead_id": "<uuid>"}`

Route handler **NÃO chama LLM**. Só persiste e enfileira. Mesmo princípio do messaging webhook (responder em <100ms).

### 3.2. Dedup de submissão

Mesma submissão chegando 2x (Respondi retry, network glitch, etc) → no-op.

**Implementação:** nova tabela `inbound_form_submissions` análoga a `inbound_messages`:

```sql
CREATE TABLE inbound_form_submissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,        -- 'respondi'
  external_id TEXT NOT NULL,     -- Respondi respondent_id
  lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
  raw JSONB NOT NULL,            -- payload bruto pra audit/replay
  field_values JSONB NOT NULL,   -- normalized
  submitted_at TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  status TEXT NOT NULL DEFAULT 'queued',  -- 'queued' | 'processed' | 'skipped_dedupe' | 'error'
  processed_at TIMESTAMPTZ,
  error_detail TEXT
);

CREATE UNIQUE INDEX uq_inbound_form_extid
  ON inbound_form_submissions (tenant_id, provider, external_id);

ALTER TABLE inbound_form_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_form_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON inbound_form_submissions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

Webhook handler faz `INSERT ... ON CONFLICT DO NOTHING`. Se conflict, retorna 200 sem enfileirar (dedup silencioso). Mesmo pattern de `inbound_messages` (Plano 5).

### 3.3. Identity resolution (resolução do Lead)

Submissão chega → precisa decidir: cria Lead novo OR reusa Lead existente?

**Política conservadora (compatível com ADR §matching errado):**

```
1. Se field_mapping mapeia `whatsapp_e164`:
     normalize phone (E.164)
     SELECT FROM leads WHERE tenant_id = ? AND whatsapp_e164 = ?
     se exists → reuse
     se não → create Lead novo

2. Se NÃO tem whatsapp_e164 (form não pediu telefone):
     ATENÇÃO: fluxo degradado — não dá pra mandar WhatsApp depois.
     log warning `form.submission.no_phone`
     marca submission como 'error' com detalhe
     return 200 (não bloqueia o webhook do form)
```

**Email NÃO é matching primário no piloto** (Manoela não vai usar email pro fluxo WhatsApp). Email vai pro CRM mas não pra resolver Lead. Plano 6 (Identity Resolver) formaliza essa lógica quando necessário.

Função `find_or_create_lead_by_form` em `forms/ingest.py` (paralela à `find_or_create_lead_by_address` em `messaging/ingest.py` — Plano 6 unifica ambas no `IdentityResolver`).

### 3.4. Worker job `process_form_inbound`

Enfileirado pelo webhook handler. Lógica:

```python
async def process_form_inbound(ctx, submission_id: str) -> None:
    async with session_factory() as session:
        # 1. Carrega submission + tenant + lead
        submission = await load_inbound_form(session, UUID(submission_id))
        await set_tenant_context(session, submission.tenant_id)
        tenant = await load_tenant(session, submission.tenant_id)
        lead = await load_lead(session, submission.lead_id)

        # 2. Resolve TreeFlow inicial do tenant.yaml > forms.<provider>.start_treeflow
        provider = submission.provider
        forms_cfg = tenant.config.forms[provider]
        treeflow_id = forms_cfg.start_treeflow

        # 3. Cria Talk pré-populado com field_values
        talk = await create_talk_with_state(
            session, tenant, lead, treeflow_id,
            preloaded_collected=submission.field_values,
        )

        # 4. Se proactive_first_message.enabled:
        if forms_cfg.proactive_first_message and forms_cfg.proactive_first_message.enabled:
            # Constrói params via Jinja2 sandbox (mesmo engine do FE-03c)
            params = render_params(forms_cfg.proactive_first_message.params, ctx={
                "collected": submission.field_values,
                "lead": {...},
            })
            # Resolve adapter messaging
            messaging_adapter = build_messaging_adapter(tenant)
            try:
                send_result = await messaging_adapter.send_template(
                    to=lead.whatsapp_e164,
                    template_ref=forms_cfg.proactive_first_message.template_ref,
                    language=forms_cfg.proactive_first_message.language,
                    params=params,
                )
                # Persiste em outbound_messages (Plano 10) — triggered_by='form_inbound'
                await record_outbound_sent(session, talk.id, send_result, triggered_by='form_inbound')
            except (PolicyError, AuthError) as exc:
                # Templates HSM erro = lead não recebe nada; operadora intervém via console
                log.error("form.proactive_first_message.failed", lead_id=lead.id, err=exc)
                talk.status = 'requires_review'
                # Não trava — submission marcada como processed, talk pra review humano

        # 5. Marca submission como processed
        submission.status = 'processed'
        submission.processed_at = utcnow()
        await session.commit()

        log.info("form.submission.processed",
                 tenant=tenant.slug, lead_id=lead.id, talk_id=talk.id)
```

**Erros que ficam fora do happy path:**
- HSM template não aprovado pela Meta → `PolicyError` → Talk vai pra `requires_review`, operadora vê no console
- `WindowExpiredError` é impossível aqui (sempre primeira mensagem)
- `RecipientUnreachable` → marca lead como unreachable, submission como processed com nota

### 3.5. RespondiFormAdapter (primeiro impl)

Respondi não documenta HMAC nativo. Estratégia de segurança: **shared secret na URL**.

```yaml
# tenant.yaml
forms:
  respondi:
    enabled: true
    shared_secret_ref: secrets/respondi_webhook_secret
```

Webhook URL configurada no Respondi inclui o secret como query param:
```
https://sdr.luminai.ia.br/webhooks/manoela-mentora/form/respondi?secret=<segredo>
```

Adapter valida `query_params["secret"] == self.secrets["respondi_webhook_secret"]` com `hmac.compare_digest`. Falha → `SignatureError` → 401.

**Parsing:**
- Payload: `{form: {form_name, form_id}, respondent: {date, respondent_id, score, status, respondent_utms, answers, raw_answers}}`
- `external_id = respondent.respondent_id`
- `submitted_at_iso = respondent.date`
- `field_values = remap(respondent.raw_answers, tenant.forms.respondi.field_mapping)`
  - `raw_answers` é mais útil que `answers` porque tem `question_id` estável (a chave do `answers` é o texto da pergunta, que muda se a operadora editar)
- `lead_identifier.whatsapp_e164 = normalize_phone(field_values.pop("whatsapp_e164"))` (se mapeado)

**Tratamento de campos específicos:**
- `question_type == "phone"` → normalize pra E.164 (lib `phonenumbers`)
- `question_type == "email"` → lowercase + validate via Pydantic `EmailStr`
- `question_type == "number"` → cast pra int/float
- Tipos exóticos (address dict, multi-select array) → mantém raw no `field_values`, deixa pro LLM resolver depois

### 3.6. Validação tenant.yaml

Schema novo em `src/ai_sdr/schemas/tenant_yaml.py`:

```python
class ProactiveFirstMessageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    template_ref: str = Field(min_length=1)
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)

class FormProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    shared_secret_ref: str | None = None
    start_treeflow: str = Field(min_length=1)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    proactive_first_message: ProactiveFirstMessageConfig | None = None

    @model_validator(mode="after")
    def _check_secret_ref(self):
        if self.enabled and not self.shared_secret_ref:
            raise ValueError("forms.<provider>.enabled=true requires shared_secret_ref")
        return self

class TenantConfig(BaseModel):
    # ... existente ...
    forms: dict[str, FormProviderConfig] = Field(default_factory=dict)
    # chave do dict = nome do provider ("respondi", "typeform", ...)
```

Validações:
- `enabled=true` exige `shared_secret_ref` (provider sem assinatura precisa de pelo menos URL secreta)
- `start_treeflow` precisa existir em `tenants/<slug>/treeflows/` (validador roda no load)
- `field_mapping` valores são checked contra os `collect:` declarados em qualquer node do `start_treeflow` (warning se mapeia campo nunca usado)

---

## 4. Modelagem: CRM write-only (saída)

### 4.1. Estrutura do subsistema

Localização proposta: `src/ai_sdr/flowengine/actions/crm/`

```
flowengine/actions/crm/
├── __init__.py            # side-effect import dos backends
├── adapter.py             # CRMActionAdapter (registra como name="crm")
├── canonical.py           # Pydantic models pro vocabulário interno (Contact, Deal canônicos)
├── backend.py             # CRMBackend ABC + registry
├── factory.py             # build_crm_backend(provider, tenant, secrets)
├── rdstation/
│   ├── __init__.py
│   ├── backend.py         # RDStationCRMBackend implements CRMBackend
│   ├── oauth.py           # OAuth2 token + refresh
│   └── client.py          # HTTP client específico (tenacity, error mapping)
└── ...                    # hubspot/, pipedrive/, etc no futuro
```

**Por que dentro de `flowengine/actions/` e não `src/ai_sdr/crm/` no top-level:**
- O `CRMActionAdapter` é fundamentalmente um `ActionAdapter` — pertence ao framework de actions
- Backends por vendor são detalhe interno do CRM adapter
- Se Fase 3 do ADR criar `src/ai_sdr/crm/` (tabelas + repositórios + sync engine), o adapter continua aqui e **importa** o módulo `crm/` pra escrita interna (futuro)

### 4.2. Canônico interno (Pydantic, alinhado ao ADR)

```python
# flowengine/actions/crm/canonical.py
from typing import Literal
from pydantic import BaseModel

DealStage = Literal["open", "won", "lost"]

class ContactCanonical(BaseModel):
    """Vocabulário PeSDR interno — não acoplado a vendor."""
    name: str
    emails: list[str] = []
    phones: list[str] = []           # E.164
    custom_fields: dict[str, str] = {}

class DealCanonical(BaseModel):
    product: str                     # "Mentoria" | "Aceleradora" | "Downsell" (Manoela)
    stage: DealStage = "open"
    value: float | None = None       # em BRL pro piloto
    currency: str = "BRL"
    qualification_notes: str | None = None  # campos coletados pelo LLM compilados
    custom_fields: dict[str, str] = {}
```

Esses são os shapes que os **handlers recebem** após renderização Jinja2.

### 4.3. CRMBackend ABC

```python
class CRMBackend(ABC):
    """Implementa handlers padronizados de CRM contra API do vendor."""
    provider: str  # class attribute

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]):
        self.tenant = tenant_config
        self.secrets = secrets
        self.crm_cfg = tenant_config.crm

    @abstractmethod
    async def create_or_update_contact(
        self, *, lead_id: UUID, contact: ContactCanonical,
    ) -> ActionResult:
        """Upsert contato. Idempotente.
        Estratégia: lookup por phone E.164 (primary key social).
        """

    @abstractmethod
    async def create_or_update_deal(
        self, *, lead_id: UUID, contact_external_id: str, deal: DealCanonical,
    ) -> ActionResult:
        """Upsert deal. Idempotente por (contact, product).
        Vincula ao contact_external_id retornado pelo handler anterior.
        """

    @abstractmethod
    async def update_deal_stage(
        self, *, deal_external_id: str, stage: DealStage,
    ) -> ActionResult:
        """Atualiza stage do deal. Mapeia stage canônico pro stage_id do vendor."""

    @abstractmethod
    async def record_qualification_note(
        self, *, contact_external_id: str, note: str,
    ) -> ActionResult:
        """Append nota ao contato (resumo da qualificação)."""
```

### 4.4. CRMActionAdapter

```python
@register  # registra no ACTION_ADAPTERS dict do FE-03c
class CRMActionAdapter(ActionAdapter):
    name = "crm"

    def __init__(self, tenant_config, secrets):
        super().__init__(tenant_config, secrets)
        if not tenant_config.crm:
            raise ConfigError(f"tenant {tenant_config.id!r}: missing crm config")
        provider = tenant_config.crm.provider
        self.backend = build_crm_backend(provider, tenant_config, secrets)

    async def execute(self, *, handler: str, params: dict) -> ActionResult:
        # Resolve handler → método do backend
        # params já vem renderizado (Jinja2) pelo dispatcher do FE-03c
        method = getattr(self.backend, handler, None)
        if method is None:
            raise UnknownHandlerError(
                f"handler {handler!r} not supported by CRM backend {self.backend.provider!r}"
            )

        # Hidra params em Pydantic canonical model
        if handler in ("create_or_update_contact",):
            contact = ContactCanonical(**params["contact"])
            return await method(lead_id=UUID(params["lead_id"]), contact=contact)
        elif handler == "create_or_update_deal":
            deal = DealCanonical(**params["deal"])
            return await method(
                lead_id=UUID(params["lead_id"]),
                contact_external_id=params["contact_external_id"],
                deal=deal,
            )
        # ... outros handlers
```

### 4.5. RDStationCRMBackend (primeiro impl)

```python
class RDStationCRMBackend(CRMBackend):
    provider = "rdstation"

    BASE_URL = "https://crm.rdstation.com/api/v1"

    def __init__(self, tenant_config, secrets):
        super().__init__(tenant_config, secrets)
        self._oauth = RDStationOAuth(secrets["rdstation_refresh_token"],
                                      secrets["rdstation_client_id"],
                                      secrets["rdstation_client_secret"])

    async def create_or_update_contact(self, *, lead_id, contact):
        # 1. Olha se já temos contact_id em Lead.crm_refs (write-through cache local)
        existing_id = await self._lookup_local_ref(lead_id, "contact_id")
        if existing_id:
            return await self._update_contact(existing_id, contact)

        # 2. Lookup remoto por phone (search endpoint do RDStation)
        remote = await self._search_contact_by_phone(contact.phones[0])
        if remote:
            await self._persist_local_ref(lead_id, "contact_id", remote["id"])
            return await self._update_contact(remote["id"], contact)

        # 3. Create novo
        token = await self._oauth.get_token()
        body = self._build_contact_body(contact)
        response = await self._http.post(
            f"{self.BASE_URL}/contacts",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        new_id = response.json()["id"]
        await self._persist_local_ref(lead_id, "contact_id", new_id)
        return ActionResult(external_id=new_id, detail={"created": True})

    async def create_or_update_deal(self, *, lead_id, contact_external_id, deal):
        # Similar — lookup local primeiro, depois remoto, depois create
        # Idempotência por (lead_id, deal.product)
        ...

    # ... outros handlers
```

**Idempotência multi-camada:**
1. **FE-03c dispatcher:** `(talk, field, value_hash)` UNIQUE — mesmo turno + mesmo valor = não re-dispara
2. **Backend lookup local:** `Lead.crm_refs.rdstation.contact_id` — se existe, update em vez de create
3. **Backend lookup remoto:** search por phone — se existe no RD Station mas não em refs locais (lead já existia lá antes), reusa
4. **Worker arq retry:** se erro transient, retry seguro porque os 3 níveis acima cobrem

### 4.6. OAuth2 do RD Station

RD Station usa OAuth2 com `access_token` (curto) + `refresh_token` (longo). Refresh quando 401.

```python
class RDStationOAuth:
    def __init__(self, refresh_token, client_id, client_secret):
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: str | None = None
        self._expires_at: datetime | None = None

    async def get_token(self) -> str:
        if self._access_token and self._expires_at > utcnow() + timedelta(seconds=60):
            return self._access_token
        await self._refresh()
        return self._access_token

    async def _refresh(self):
        # POST /oauth/token com refresh_token
        # Persiste novo access_token + expires_at em memória do processo
        # NOTA: refresh_token também pode rotacionar — atualizar secrets.enc.yaml
        # via job manual ou alerta pro operador
        ...
```

**Edge case crítico:** se RD Station rotacionar o `refresh_token` (alguns OAuth providers fazem), o adapter precisa persistir o novo. SOPS não é editável programaticamente em runtime de forma trivial. **Proposta:** se o refresh retornar novo refresh_token, emit alert `crm.rdstation.refresh_token_rotated` no log + falha o worker job pra HITL. Operador atualiza secrets.enc.yaml manualmente.

Alternativa: persistir refresh_token em row separada no DB (`crm_tokens` table). Mais robusto mas adiciona complexidade. Decidir no Plano 7 execution.

### 4.7. Tenant.yaml — bloco `crm`

```python
class CRMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1)          # "rdstation" | "hubspot" | ...
    # campos por provider — vão num sub-bloco específico
    rdstation: RDStationCRMConfig | None = None
    # hubspot: HubSpotCRMConfig | None = None
    # ... outros

    @model_validator(mode="after")
    def _check_provider_block_present(self):
        if not getattr(self, self.provider, None):
            raise ValueError(f"crm.provider={self.provider!r} requires crm.{self.provider}: {{...}} block")
        return self

class RDStationCRMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token_ref: str            # secrets/rdstation_refresh_token
    client_id_ref: str                # secrets/rdstation_client_id
    client_secret_ref: str            # secrets/rdstation_client_secret
    pipeline_id: str                  # ID do pipeline onde criar deals
    stage_mapping: dict[Literal["open", "won", "lost"], str]
    # opcional: mapping de campos customizados
    custom_field_mapping: dict[str, str] = Field(default_factory=dict)
```

Exemplo:

```yaml
crm:
  provider: rdstation
  rdstation:
    refresh_token_ref: secrets/rdstation_refresh_token
    client_id_ref: secrets/rdstation_client_id
    client_secret_ref: secrets/rdstation_client_secret
    pipeline_id: "abc123"             # configurado no painel RD Station
    stage_mapping:
      open: "stage_lead_id"
      won: "stage_ganho_id"
      lost: "stage_perdido_id"
    custom_field_mapping:
      faturamento_mensal: "cf_faturamento_mensal"
      tempo_mercado: "cf_tempo_mercado"
```

### 4.8. TreeFlow YAML — actions de CRM

No `qualificacao_inicial.yaml` da Manoela:

```yaml
nodes:
  - id: saudacao
    collect:
      - field: nome
        required: true
    # Quando nome é coletado (ou pré-carregado pelo form), cria contact no CRM
    on_collected:
      - field: nome
        adapter: crm
        handler: create_or_update_contact
        params:
          lead_id: "{{ lead.id }}"
          contact:
            name: "{{ collected.nome }}"
            phones: ["{{ lead.whatsapp_e164 }}"]
            custom_fields: {}
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "is_set('nome')"
        target: qualificacao

  - id: qualificacao
    collect:
      - field: faturamento_mensal
        required: true
    on_collected:
      - field: faturamento_mensal
        adapter: crm
        handler: create_or_update_deal
        params:
          lead_id: "{{ lead.id }}"
          # contact_external_id resolvido lendo Lead.crm_refs no backend
          # (Jinja2 não tem acesso ao DB; backend faz lookup)
          contact_external_id: "{{ lead.crm_refs.rdstation.contact_id | default('') }}"
          deal:
            product: "Mentoria"     # ou "Aceleradora" — pode ser condicional via Jinja2
            stage: "open"
            qualification_notes: "Faturamento mensal: R$ {{ collected.faturamento_mensal }}"
            custom_fields:
              faturamento_mensal: "{{ collected.faturamento_mensal }}"
    exit_condition:
      type: rule_expression
      expression: "faturamento_mensal != None"
    next_nodes:
      - condition: "true"
        target: END
```

**Sutileza:** `contact_external_id` precisa estar disponível no contexto Jinja2 do dispatcher. Solução: expor `lead.crm_refs` no `build_template_context` do FE-03c. Mudança pequena, retro-compatible.

### 4.9. Mapeamento de erros do RD Station

| HTTP status / situação | Exception PeSDR | Comportamento worker |
|---|---|---|
| 401 (token expirado) | (interno) | refresh token + retry mesmo job (incrementa attempts) |
| 401 (refresh falhou) | `AuthError` | terminal failure, alert pra operador |
| 403 (permissão) | `AuthError` | terminal failure, alert |
| 422 (validation, ex: phone inválido) | `ValidationError` (custom) | terminal failure (não retry — payload ruim) |
| 404 (deal/contact deletado externamente) | `RemoteResourceGone` | terminal failure, marca refs como stale em `Lead.crm_refs.rdstation._stale = true` |
| 429 (rate limit) | `RateLimitError(retry_after)` | tenacity retry interno (3x), respeita Retry-After |
| 5xx, network, timeout | `TransientError` | tenacity retry interno (3x) → se persiste, escala pra worker retry (3x) |

---

## 5. Mudanças de schema

### 5.1. Migration nova (próximo número disponível, ~0030 ou superior)

```sql
-- 1. Lead.crm_refs JSONB
ALTER TABLE leads ADD COLUMN crm_refs JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX ix_leads_crm_refs_gin ON leads USING GIN (crm_refs);

-- 2. inbound_form_submissions table (paralelo a inbound_messages)
CREATE TABLE inbound_form_submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    raw JSONB NOT NULL,
    field_values JSONB NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processed', 'skipped_dedupe', 'error')),
    processed_at TIMESTAMPTZ,
    error_detail TEXT
);

CREATE UNIQUE INDEX uq_inbound_form_extid
    ON inbound_form_submissions (tenant_id, provider, external_id);
CREATE INDEX ix_inbound_form_lead_status
    ON inbound_form_submissions (lead_id, status) WHERE status IN ('queued', 'error');

ALTER TABLE inbound_form_submissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_form_submissions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON inbound_form_submissions
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

**Nada de Contact/Deal/Organization tables.** ADR Fase 3 cria depois.

### 5.2. Tenant.yaml schema additions

Resumido (ver §3.6 e §4.7 acima pra Pydantic completo):

```yaml
# Existente — não muda
id: manoela-mentora
display_name: "Manoela Mentora"
messaging: { ... }
console: { ... }
guardrails: { ... }
objections: { ... }

# Novo — Form ingestion
forms:
  respondi:
    enabled: true
    shared_secret_ref: secrets/respondi_webhook_secret
    start_treeflow: qualificacao_inicial
    field_mapping:
      qst_<id_da_pergunta_nome>: nome
      qst_<id_da_pergunta_phone>: whatsapp_e164
      qst_<id_da_pergunta_faturamento>: faturamento_mensal
    proactive_first_message:
      enabled: true
      template_ref: "saudacao_proativa_v1"
      language: pt_BR
      params:
        - "{{ collected.nome | default('') }}"

# Novo — CRM out
crm:
  provider: rdstation
  rdstation:
    refresh_token_ref: secrets/rdstation_refresh_token
    client_id_ref: secrets/rdstation_client_id
    client_secret_ref: secrets/rdstation_client_secret
    pipeline_id: "<id_do_pipeline_no_painel>"
    stage_mapping:
      open: "<stage_id_lead>"
      won: "<stage_id_ganho>"
      lost: "<stage_id_perdido>"
    custom_field_mapping:
      faturamento_mensal: "cf_faturamento_mensal"
```

### 5.3. Secrets.enc.yaml adicionais (Manoela)

```yaml
# já existem (cifrados):
anthropic_key: ...
openai_key: ...
wa_phone_id: ...
wa_token: ...
wa_verify: ...
wa_app_secret: ...

# novos (precisam ser obtidos pelo Pedro):
respondi_webhook_secret: <string aleatória que Pedro gera + cola na URL do webhook Respondi>
rdstation_client_id: <do app criado no painel RD Station Developers>
rdstation_client_secret: <do app>
rdstation_refresh_token: <obtido via OAuth flow inicial, uma vez>
```

### 5.4. Resumo de mudanças por arquivo

| Categoria | Arquivo | Mudança |
|---|---|---|
| Migration | `migrations/versions/0030_form_ingestion_and_crm_refs.py` | NOVO — DDL acima |
| Schema | `src/ai_sdr/schemas/tenant_yaml.py` | Adiciona `FormProviderConfig`, `CRMConfig`, `RDStationCRMConfig`, `ProactiveFirstMessageConfig` |
| Modelo | `src/ai_sdr/models/inbound_form_submission.py` | NOVO ORM |
| Modelo | `src/ai_sdr/models/lead.py` | Add `crm_refs: Mapped[dict]` |
| Repositório | `src/ai_sdr/repositories/inbound_form_repository.py` | NOVO (find-or-create, mark processed, etc) |
| Form subsistema | `src/ai_sdr/forms/{__init__,base,registry,factory,errors,ingest,respondi}.py` | NOVO package |
| Route | `src/ai_sdr/api/routes/forms.py` | NOVO `POST /webhooks/{slug}/form/{provider}` |
| Route registration | `src/ai_sdr/main.py` | Add `forms_router` |
| Worker job | `src/ai_sdr/worker/jobs/process_form_inbound.py` | NOVO |
| Worker register | `src/ai_sdr/worker/main.py` | Add ao `WorkerSettings.functions` |
| CRM subsistema | `src/ai_sdr/flowengine/actions/crm/{__init__,adapter,canonical,backend,factory}.py` | NOVO |
| CRM RD Station | `src/ai_sdr/flowengine/actions/crm/rdstation/{__init__,backend,oauth,client}.py` | NOVO |
| Action register | `src/ai_sdr/flowengine/actions/__init__.py` | Add `from .crm import adapter as _crm_adapter  # noqa` |
| Template context | `src/ai_sdr/flowengine/actions/templating.py` | Add `lead.crm_refs` ao context |
| Tenant Manoela | `tenants/manoela-mentora/tenant.yaml` | Add blocos `forms.respondi` + `crm.rdstation` |
| Tenant Manoela | `tenants/manoela-mentora/secrets.enc.yaml` | Add 4 chaves novas (cifradas) |
| TreeFlow Manoela | `tenants/manoela-mentora/treeflows/qualificacao_inicial.yaml` | Add `on_collected` com `adapter: crm` |
| CLAUDE.md | `CLAUDE.md` | Nova seção "Form ingestion + CRM (write-only)" |

Pacote enxuto. ~22 arquivos novos/modificados, sem refactor de core.

---

## 6. Fluxo end-to-end (cenário Manoela)

```
┌──────────────────────────────────────────────────────────────────────┐
│ T0 — Lead preenche formulário Respondi (Manoela)                     │
└──────────────────────────────────────────────────────────────────────┘
        ↓
        Respondi → POST https://sdr.luminai.ia.br/webhooks/manoela-mentora/form/respondi?secret=<...>
        body: { form: {...}, respondent: { respondent_id, answers, raw_answers, ... } }
        ↓
┌──────────────────────────────────────────────────────────────────────┐
│ T1 — FastAPI route /webhooks/{slug}/form/{provider}                  │
└──────────────────────────────────────────────────────────────────────┘
        - resolve tenant (slug) + adapter (provider)
        - adapter.handle_submission(raw_body, headers, query_params)
            - valida secret → SignatureError → 401
            - parseia raw_answers → field_values normalizados
            - extrai lead_identifier (whatsapp_e164 do form)
        - find_or_create_lead_by_form(tenant, lead_identifier)
        - INSERT inbound_form_submissions ON CONFLICT DO NOTHING
        - arq.enqueue_job("process_form_inbound", submission_id)
        - retorna 200 em <100ms

┌──────────────────────────────────────────────────────────────────────┐
│ T2 — Worker job process_form_inbound                                 │
└──────────────────────────────────────────────────────────────────────┘
        - set_tenant_context
        - load submission + tenant + lead
        - resolve start_treeflow (config) + load TreeFlowVersion
        - create Talk com TalkFlowState pré-populado:
            collected = field_values  (nome=Maria, faturamento_mensal=40000)
            current_node = entry_node (saudacao)
        - se proactive_first_message.enabled:
            - render params via Jinja2
            - messaging_adapter.send_template(...)
            - record_outbound triggered_by='form_inbound'
        - mark submission processed
        - Talk fica em status=active aguardando lead responder
        - retorna

(Tempo decorrido: <2s desde Respondi → primeira mensagem WhatsApp enviada)

┌──────────────────────────────────────────────────────────────────────┐
│ T3 — Lead responde no WhatsApp                                        │
└──────────────────────────────────────────────────────────────────────┘
        WhatsApp → POST /webhooks/manoela-mentora/whatsapp_cloud
        - (mesmo pipeline de antes, Plano 5)
        - INSERT inbound_message, enqueue process_lead_inbox

┌──────────────────────────────────────────────────────────────────────┐
│ T4 — Worker job process_lead_inbox (existente, Plano 5 + FE-01b)      │
└──────────────────────────────────────────────────────────────────────┘
        - pega Talk associado ao Lead (criado em T2)
        - run_turn(talk, inbound):
            - preprocessing
            - LLM (com state.collected já pré-populado)
            - apply_decision (merge collected_fields novos)
            - **dispatch_actions** (FE-03c)
                - itera node.on_collected (do TreeFlow YAML)
                - pra cada action cujo field foi coletado neste turno:
                    - render params via Jinja2 (incl. lead.crm_refs)
                    - INSERT action_executions ON CONFLICT DO NOTHING
                    - arq.enqueue_job("execute_action", execution_id)
            - close_lifecycle
            - messaging_adapter.send_text(reply)
            - audit outbound

┌──────────────────────────────────────────────────────────────────────┐
│ T5 — Worker job execute_action (FE-03c)                              │
└──────────────────────────────────────────────────────────────────────┘
        - load action_execution
        - build_action_adapter("crm", tenant) → CRMActionAdapter
            - lê tenant.crm.provider = "rdstation"
            - build_crm_backend("rdstation", tenant, secrets) → RDStationCRMBackend
        - adapter.execute(handler=action.handler, params=action.params_resolved)
            - CRMActionAdapter despacha pro backend method
            - RDStationCRMBackend.create_or_update_contact(lead_id, contact)
                - lookup Lead.crm_refs.rdstation.contact_id (local)
                - se não → search RD Station API por phone
                - se não → POST /api/v1/contacts (cria)
                - atualiza Lead.crm_refs.rdstation.contact_id (UPDATE)
                - retorna ActionResult(external_id=<id_rd_station>)
        - mark action_execution status=success, external_id=<id>
        - retorna

┌──────────────────────────────────────────────────────────────────────┐
│ T6 — Próximo turno: faturamento coletado                              │
└──────────────────────────────────────────────────────────────────────┘
        - Pipeline igual a T4-T5, mas dispatcher pega `on_collected` do node
          `qualificacao` (handler create_or_update_deal)
        - Backend usa contact_external_id resolvido no template (lê de
          Lead.crm_refs.rdstation.contact_id)
        - Cria deal no RD Station vinculado ao contato
        - Lead.crm_refs.rdstation.deal_id atualizado

┌──────────────────────────────────────────────────────────────────────┐
│ T7 — Talk fecha (talk_lifecycle.close_when_completed)                 │
└──────────────────────────────────────────────────────────────────────┘
        - Lifecycle close (FE-03b) detecta condição
        - Optional future: on_close action que atualiza stage do deal pra
          "won" ou "lost" no RD Station — não no MVP, FE-04+

(Operadora Lana pode acompanhar tudo via Console HITL durante o processo)
```

**Pontos de monitoramento:**
- `inbound_form_submissions.status` — visibilidade de form ingestion
- `action_executions.status` + `external_id` — visibilidade de CRM sync
- `outbound_messages.triggered_by` — diferencia mensagens disparadas por form vs lead
- Lead.crm_refs — snapshot por tenant do estado de sync
- Logs estruturados: `form.submission.*`, `crm.rdstation.*`, `action.executed`

---

## 7. Escalabilidade — multi-form e multi-CRM

### 7.1. Outro form provider (ex: Typeform)

Custo: ~120 LOC + tests.

1. Cria `src/ai_sdr/forms/typeform.py` implementando `FormProviderAdapter`
2. Registra com `@register` (name="typeform")
3. Tenant que usar Typeform adiciona no `tenant.yaml`:
   ```yaml
   forms:
     typeform:
       enabled: true
       hmac_secret_ref: secrets/typeform_hmac     # Typeform suporta HMAC, melhor que secret na URL
       start_treeflow: <id>
       field_mapping: { ... }
       proactive_first_message: { ... }
   ```
4. Webhook URL Typeform: `/webhooks/<slug>/form/typeform`
5. Nada mais muda — router resolve dinâmicamente, registry encontra adapter, worker job é o mesmo

**Diferença chave:** se um provider suporta HMAC (Typeform sim, Respondi não), o `FormProviderAdapter` específico faz a verificação no `handle_submission`. Contract é abstrato — cada impl decide segurança.

### 7.2. Outro tenant com mesmo form provider

Ex: novo cliente "Cliente B" também usa Respondi.

1. Cria `tenants/cliente-b/tenant.yaml` com bloco `forms.respondi` próprio (e shared_secret diferente)
2. Cria `tenants/cliente-b/secrets.enc.yaml` com `respondi_webhook_secret` próprio
3. Webhook URL: `/webhooks/cliente-b/form/respondi?secret=<próprio>`
4. Zero código — mesmo adapter, instância por tenant via factory

### 7.3. Outro CRM (ex: HubSpot)

Custo: ~250 LOC + tests (mais OAuth do HubSpot é diferente do RD Station).

1. Cria `src/ai_sdr/flowengine/actions/crm/hubspot/{backend,oauth,client}.py`
2. `HubSpotCRMBackend(CRMBackend)` implementa os mesmos handlers (create_or_update_contact, etc) batendo na API do HubSpot
3. Registra em `flowengine/actions/crm/backend.py` registry
4. Tenant que usa HubSpot:
   ```yaml
   crm:
     provider: hubspot
     hubspot:
       access_token_ref: secrets/hubspot_token
       pipeline_id: "..."
       stage_mapping: { ... }
   ```
5. TreeFlow YAML **não muda** — `adapter: crm, handler: create_or_update_contact` resolve via tenant config

**Validação:** ADR §"canônico nasce como modelo de domínio" garante que o vocabulário (`stage: open|won|lost`, `ContactCanonical`, `DealCanonical`) é o mesmo em todos os backends. Cada backend traduz pra vocabulário do vendor.

### 7.4. Tenant SEM CRM (cliente sem CRM próprio — caso comum no ICP)

Configurar `tenant.yaml` SEM bloco `crm:`. TreeFlow YAML pode ter `on_collected: adapter: crm`, mas como não há config, o adapter resolve com `CRMNullBackend` (no-op).

Alternativa cleaner: tenant.yaml com `crm: provider: null` ou `crm` ausente → TreeFlow loader emite warning e o dispatcher do FE-03c skipa actions com `adapter: crm` (já tem branching pra unknown adapter — `objection.classifier.error` style).

Decisão pra Plano 7 executar.

### 7.5. Mudança de CRM mid-stream (raro mas plausível)

Cliente troca de RD Station pra HubSpot. Como o `Lead.crm_refs.rdstation.*` continua existindo, mas agora `tenant.crm.provider = "hubspot"`:

- Action handlers passam a chamar `HubSpotCRMBackend`
- `Lead.crm_refs.hubspot.*` começa a ser populado em paralelo
- `Lead.crm_refs.rdstation.*` fica como histórico (não removido)

Sem migração de dados — Fase 4 do ADR (sync bidirecional) eventualmente reconcilia.

---

## 8. Plano de implementação — código concreto

> Esta seção é o coração executivo da spec. Detalha **o que será criado, em qual ordem, com quais contracts**. Cada tarefa tem escopo fechado pra virar 1 commit (eventualmente 1 PR menor).

### 8.1. Estrutura final de pastas esperada

Depois das 3 fases (A + B + C), a árvore do projeto deve ter:

```
src/ai_sdr/
├── forms/                                      # NOVO subsistema (Fase A)
│   ├── __init__.py
│   ├── base.py                                 # FormProviderAdapter ABC + IngestedFormSubmission
│   ├── registry.py                             # FORM_PROVIDERS + @register
│   ├── factory.py                              # build_form_adapter(name, tenant, secrets)
│   ├── errors.py                               # SignatureError, MalformedPayload, FormProviderError
│   ├── ingest.py                               # find_or_create_lead_by_form + create_talk_with_state
│   └── respondi.py                             # RespondiFormAdapter
│
├── flowengine/actions/crm/                     # NOVO subsistema (Fase B)
│   ├── __init__.py                             # side-effect import dos backends
│   ├── adapter.py                              # CRMActionAdapter (@register name="crm")
│   ├── canonical.py                            # ContactCanonical, DealCanonical, DealStage
│   ├── backend.py                              # CRMBackend ABC + CRM_BACKENDS registry
│   ├── factory.py                              # build_crm_backend(provider, tenant, secrets)
│   ├── errors.py                               # AuthError, RemoteResourceGone, ValidationError
│   └── rdstation/                              # primeiro backend
│       ├── __init__.py
│       ├── backend.py                          # RDStationCRMBackend
│       ├── oauth.py                            # RDStationOAuth (token + refresh)
│       └── client.py                           # HTTP client + error mapping
│
├── api/routes/
│   └── forms.py                                # NOVO — GET/POST /webhooks/{slug}/form/{provider}
│
├── worker/jobs/
│   └── process_form_inbound.py                 # NOVO worker job
│
├── models/
│   ├── inbound_form_submission.py              # NOVO ORM
│   └── lead.py                                 # MODIFICADO — add crm_refs JSONB column
│
├── repositories/
│   └── inbound_form_repository.py              # NOVO
│
├── schemas/
│   └── tenant_yaml.py                          # MODIFICADO — add FormProviderConfig + CRMConfig
│
└── flowengine/actions/templating.py            # MODIFICADO — add lead.crm_refs ao context

migrations/versions/
└── 0030_form_ingestion_and_crm_refs.py         # NOVO

tenants/manoela-mentora/                        # Fase C wiring
├── tenant.yaml                                 # MODIFICADO — add forms + crm blocks
├── secrets.enc.yaml                            # MODIFICADO — add 4 chaves novas (cifradas via sops)
└── treeflows/
    └── qualificacao_inicial.yaml               # MODIFICADO — add on_collected: crm (bump v0.3.0)

tests/
├── unit/
│   ├── test_form_provider_base.py              # NOVO
│   ├── test_form_respondi_adapter.py           # NOVO
│   ├── test_form_registry_and_factory.py       # NOVO
│   ├── test_form_ingest.py                     # NOVO
│   ├── test_crm_canonical_models.py            # NOVO
│   ├── test_crm_action_adapter.py              # NOVO
│   ├── test_crm_backend_registry.py            # NOVO
│   ├── test_rdstation_oauth.py                 # NOVO
│   ├── test_rdstation_backend.py               # NOVO (mock httpx)
│   ├── test_tenant_yaml_forms_config.py        # NOVO
│   └── test_tenant_yaml_crm_config.py          # NOVO
└── integration/
    ├── test_inbound_form_submissions_rls.py    # NOVO
    ├── test_form_webhook_route.py              # NOVO
    ├── test_process_form_inbound_worker.py     # NOVO
    ├── test_crm_action_dispatch_e2e.py         # NOVO
    └── test_rdstation_smoke.py                 # NOVO (gated por env var, hits sandbox)

tests/fixtures/
├── respondi/
│   ├── submission_text_form.json               # NOVO — payload real capturado
│   └── submission_with_utms.json               # NOVO
└── rdstation/
    ├── create_contact_response.json            # NOVO
    └── create_deal_response.json               # NOVO
```

**Totalizando aproximadamente:**
- 22 arquivos de produção novos
- 16 arquivos de teste novos
- 5 arquivos modificados
- 6 fixtures novas

### 8.2. Code stubs dos contracts principais

#### 8.2.1. `forms/base.py` — FormProviderAdapter ABC

```python
"""Contract for form submission ingestion (4ª borda do PeSDR)."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, model_validator


class LeadIdentifier(BaseModel):
    """Como o Lead será resolvido (find-or-create) pelo `forms.ingest`."""
    whatsapp_e164: str | None = None
    email: str | None = None
    external_label: str | None = None

    @model_validator(mode="after")
    def _require_at_least_one(self):
        if not any([self.whatsapp_e164, self.email, self.external_label]):
            raise ValueError("LeadIdentifier requires at least one of whatsapp_e164/email/external_label")
        return self


@dataclass(frozen=True)
class IngestedFormSubmission:
    """Output normalizado do FormProviderAdapter.handle_submission."""
    external_id: str
    submitted_at_iso: str
    lead_identifier: LeadIdentifier
    field_values: dict[str, Any]
    source_meta: dict[str, Any] = field(default_factory=dict)


class FormProviderAdapter(ABC):
    """
    Implementa entrada de leads via formulários externos. Pure: zero conhecimento
    de leads/tenants tables; só normaliza payload + valida assinatura.
    """
    name: str  # class attribute, registry key

    def __init__(self, tenant_config, secrets: dict[str, str]):
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def handle_submission(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
    ) -> IngestedFormSubmission:
        """
        Valida + parseia + normaliza.

        Raises:
            SignatureError: se HMAC/secret URL não confere
            MalformedPayload: se shape inesperado
        """
```

#### 8.2.2. `forms/respondi.py` — primeiro impl

```python
"""Respondi.app form provider — webhook + payload normalization."""
from __future__ import annotations
import hmac
from collections.abc import Mapping
from typing import Any

from ai_sdr.forms.base import FormProviderAdapter, IngestedFormSubmission, LeadIdentifier
from ai_sdr.forms.errors import SignatureError, MalformedPayload
from ai_sdr.forms.registry import register

import phonenumbers
import json


@register
class RespondiFormAdapter(FormProviderAdapter):
    name = "respondi"

    async def handle_submission(self, raw_body, headers, query_params):
        # 1. Valida shared_secret na URL (Respondi não suporta HMAC nativo)
        expected = self.secrets.get("respondi_webhook_secret", "")
        received = query_params.get("secret", "")
        if not expected or not hmac.compare_digest(expected, received):
            raise SignatureError("respondi: invalid or missing secret in query string")

        # 2. Parse JSON
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise MalformedPayload(f"respondi: invalid JSON: {exc}")

        if "respondent" not in payload or "respondent_id" not in payload.get("respondent", {}):
            raise MalformedPayload("respondi: missing respondent.respondent_id")

        respondent = payload["respondent"]

        # 3. Normalize raw_answers via field_mapping
        forms_cfg = self.tenant.forms.get(self.name)
        if forms_cfg is None:
            raise MalformedPayload(f"tenant {self.tenant.id!r} has no forms.{self.name} config")
        mapping = forms_cfg.field_mapping

        field_values: dict[str, Any] = {}
        whatsapp_e164: str | None = None
        for raw in respondent.get("raw_answers", []):
            qid = raw["question"]["question_id"]
            target_field = mapping.get(qid)
            if target_field is None:
                continue
            answer = self._coerce_answer(raw)
            if target_field == "whatsapp_e164":
                whatsapp_e164 = self._normalize_phone(answer)
            else:
                field_values[target_field] = answer

        if not whatsapp_e164:
            raise MalformedPayload(
                f"respondi: field_mapping must include a question mapped to whatsapp_e164 "
                f"(received raw_answers question_ids: {[r['question']['question_id'] for r in respondent.get('raw_answers', [])]})"
            )

        return IngestedFormSubmission(
            external_id=str(respondent["respondent_id"]),
            submitted_at_iso=respondent["date"],
            lead_identifier=LeadIdentifier(whatsapp_e164=whatsapp_e164),
            field_values=field_values,
            source_meta={
                "form_id": payload.get("form", {}).get("form_id"),
                "form_name": payload.get("form", {}).get("form_name"),
                "utms": respondent.get("respondent_utms", {}),
                "score": respondent.get("score"),
                "status": respondent.get("status"),
            },
        )

    def _coerce_answer(self, raw_answer: dict) -> Any:
        qtype = raw_answer["question"]["question_type"]
        ans = raw_answer["answer"]
        if qtype == "number" and isinstance(ans, str):
            try:
                return int(ans) if ans.isdigit() else float(ans)
            except ValueError:
                return ans
        if qtype == "email" and isinstance(ans, str):
            return ans.strip().lower()
        return ans

    def _normalize_phone(self, raw: Any) -> str:
        try:
            parsed = phonenumbers.parse(str(raw), "BR")
            if not phonenumbers.is_valid_number(parsed):
                raise MalformedPayload(f"respondi: invalid phone {raw!r}")
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException as exc:
            raise MalformedPayload(f"respondi: invalid phone {raw!r}: {exc}")
```

#### 8.2.3. `api/routes/forms.py` — webhook route

```python
"""Inbound form webhooks. URL shape: /webhooks/{tenant_slug}/form/{provider}"""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import db_session, adapter_registry, arq_pool
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.forms.errors import SignatureError, MalformedPayload
from ai_sdr.forms.ingest import find_or_create_lead_by_form
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.tenant_loader.loader import TenantNotFoundError

router = APIRouter()


@router.post("/webhooks/{tenant_slug}/form/{provider}")
async def form_webhook_ingest(
    tenant_slug: str,
    provider: str,
    request: Request,
    session: AsyncSession = Depends(db_session),
    registry = Depends(adapter_registry),
    pool = Depends(arq_pool),
):
    try:
        tenant = registry.tenant_loader.load(tenant_slug)
    except TenantNotFoundError:
        raise HTTPException(404, "tenant not found")

    if provider not in (tenant.forms or {}) or not tenant.forms[provider].enabled:
        raise HTTPException(404, "form provider not enabled for tenant")

    secrets = registry.sops_loader.load(tenant_slug)
    adapter = registry.get_form_adapter(tenant, provider, secrets)

    raw_body = await request.body()
    try:
        submission = await adapter.handle_submission(
            raw_body=raw_body,
            headers=request.headers,
            query_params=request.query_params,
        )
    except SignatureError:
        raise HTTPException(401, "invalid signature")
    except MalformedPayload as exc:
        raise HTTPException(400, str(exc))

    await set_tenant_context(session, tenant.id)
    lead = await find_or_create_lead_by_form(session, tenant, submission.lead_identifier)

    stmt = (
        pg_insert(InboundFormSubmission)
        .values(
            tenant_id=tenant.id,
            provider=provider,
            external_id=submission.external_id,
            lead_id=lead.id,
            raw=dict(json.loads(raw_body)),
            field_values=submission.field_values,
            submitted_at=submission.submitted_at_iso,
            status="queued",
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "provider", "external_id"])
        .returning(InboundFormSubmission.id)
    )
    result = await session.execute(stmt)
    new_id = result.scalar_one_or_none()
    await session.commit()

    if new_id is None:
        return {"status": "skipped_dedupe", "lead_id": str(lead.id)}

    await pool.enqueue_job("process_form_inbound", str(new_id))
    return {"status": "queued", "lead_id": str(lead.id), "submission_id": str(new_id)}
```

#### 8.2.4. `flowengine/actions/crm/adapter.py` — CRMActionAdapter

```python
"""CRM action adapter — vendor-agnostic, dispatches to backend."""
from typing import Any
from uuid import UUID

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.registry import register
from ai_sdr.flowengine.actions.crm.canonical import ContactCanonical, DealCanonical, DealStage
from ai_sdr.flowengine.actions.crm.factory import build_crm_backend
from ai_sdr.flowengine.actions.crm.errors import UnknownHandlerError


@register
class CRMActionAdapter(ActionAdapter):
    name = "crm"

    def __init__(self, tenant_config, secrets):
        super().__init__(tenant_config, secrets)
        if not getattr(tenant_config, "crm", None):
            raise ValueError(f"tenant {tenant_config.id!r}: missing crm config block")
        self.backend = build_crm_backend(tenant_config.crm.provider, tenant_config, secrets)

    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        if handler == "create_or_update_contact":
            return await self.backend.create_or_update_contact(
                lead_id=UUID(params["lead_id"]),
                contact=ContactCanonical(**params["contact"]),
            )
        if handler == "create_or_update_deal":
            return await self.backend.create_or_update_deal(
                lead_id=UUID(params["lead_id"]),
                contact_external_id=params["contact_external_id"],
                deal=DealCanonical(**params["deal"]),
            )
        if handler == "update_deal_stage":
            return await self.backend.update_deal_stage(
                deal_external_id=params["deal_external_id"],
                stage=DealStage(params["stage"]),
            )
        if handler == "record_qualification_note":
            return await self.backend.record_qualification_note(
                contact_external_id=params["contact_external_id"],
                note=params["note"],
            )
        raise UnknownHandlerError(f"CRMActionAdapter: handler {handler!r} not supported")
```

#### 8.2.5. `flowengine/actions/crm/rdstation/backend.py` — primeiro backend

```python
"""RD Station CRM backend — write-only Fase 1 do ADR CRM."""
from uuid import UUID
import structlog

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.flowengine.actions.crm.backend import CRMBackend
from ai_sdr.flowengine.actions.crm.canonical import ContactCanonical, DealCanonical, DealStage
from ai_sdr.flowengine.actions.crm.errors import (
    AuthError, RemoteResourceGone, ValidationError, RateLimitError, TransientError,
)
from ai_sdr.flowengine.actions.crm.rdstation.client import RDStationClient
from ai_sdr.flowengine.actions.crm.rdstation.oauth import RDStationOAuth

log = structlog.get_logger(__name__)


class RDStationCRMBackend(CRMBackend):
    provider = "rdstation"

    def __init__(self, tenant_config, secrets):
        super().__init__(tenant_config, secrets)
        cfg = tenant_config.crm.rdstation
        self.oauth = RDStationOAuth(
            refresh_token=secrets[cfg.refresh_token_ref.removeprefix("secrets/")],
            client_id=secrets[cfg.client_id_ref.removeprefix("secrets/")],
            client_secret=secrets[cfg.client_secret_ref.removeprefix("secrets/")],
        )
        self.client = RDStationClient(self.oauth)
        self.pipeline_id = cfg.pipeline_id
        self.stage_mapping = cfg.stage_mapping
        self.custom_field_mapping = cfg.custom_field_mapping

    async def create_or_update_contact(self, *, lead_id: UUID, contact: ContactCanonical) -> ActionResult:
        # 1. Check Lead.crm_refs (local cache)
        local_id = await self._lookup_local_ref(lead_id, "contact_id")
        if local_id:
            return await self._update_contact(local_id, contact)

        # 2. Search remote by phone
        primary_phone = contact.phones[0] if contact.phones else None
        if primary_phone:
            remote = await self.client.search_contact_by_phone(primary_phone)
            if remote:
                await self._persist_local_ref(lead_id, "contact_id", remote["id"])
                return await self._update_contact(remote["id"], contact)

        # 3. Create
        body = self._build_contact_body(contact)
        created = await self.client.create_contact(body)
        await self._persist_local_ref(lead_id, "contact_id", created["id"])
        log.info("crm.rdstation.contact_created", lead_id=str(lead_id), external_id=created["id"])
        return ActionResult(external_id=created["id"], detail={"created": True})

    async def create_or_update_deal(self, *, lead_id, contact_external_id, deal):
        # Idempotência: dedup por (lead_id, deal.product) em Lead.crm_refs
        ref_key = f"deal_id_{deal.product.lower().replace(' ', '_')}"
        local_id = await self._lookup_local_ref(lead_id, ref_key)
        if local_id:
            return await self._update_deal(local_id, deal)

        body = self._build_deal_body(contact_external_id, deal)
        created = await self.client.create_deal(body)
        await self._persist_local_ref(lead_id, ref_key, created["id"])
        log.info("crm.rdstation.deal_created", lead_id=str(lead_id), external_id=created["id"], product=deal.product)
        return ActionResult(external_id=created["id"], detail={"created": True, "product": deal.product})

    # ... update_deal_stage, record_qualification_note, _lookup_local_ref, _persist_local_ref ...

    def _build_contact_body(self, contact: ContactCanonical) -> dict:
        return {
            "contact": {
                "name": contact.name,
                "emails": [{"email": e} for e in contact.emails],
                "phones": [{"phone": p, "type": "cellphone"} for p in contact.phones],
                # custom_fields traduzidos via custom_field_mapping
                **{self.custom_field_mapping.get(k, k): v for k, v in contact.custom_fields.items()},
            }
        }

    def _build_deal_body(self, contact_external_id: str, deal: DealCanonical) -> dict:
        return {
            "deal": {
                "name": f"{deal.product} — auto",
                "deal_pipeline": {"id": self.pipeline_id},
                "deal_stage": {"id": self.stage_mapping[deal.stage]},
                "amount_montly": deal.value,
            },
            "contacts": [{"id": contact_external_id}],
            **{self.custom_field_mapping.get(k, k): v for k, v in deal.custom_fields.items()},
        }
```

#### 8.2.6. `worker/jobs/process_form_inbound.py`

```python
"""Async worker job: process inbound form submission → create Talk + send proactive HSM."""
from uuid import UUID
import structlog

from ai_sdr.db.session import session_factory
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.forms.ingest import create_talk_with_state
from ai_sdr.flowengine.actions.templating import render_params
from ai_sdr.messaging.factory import build_messaging_adapter
from ai_sdr.messaging.errors import PolicyError, AuthError, RecipientUnreachable, MessagingError
from ai_sdr.models.inbound_form_submission import InboundFormSubmission
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.secrets.sops_loader import SopsLoader

log = structlog.get_logger(__name__)


async def process_form_inbound(ctx, submission_id: str) -> None:
    sub_id = UUID(submission_id)
    async with session_factory() as session:
        # Worker is trusted; needs cross-tenant lookup of submission
        await session.execute(text("SET LOCAL row_security = off"))
        sub = await session.get(InboundFormSubmission, sub_id)
        if sub is None:
            log.info("form.submission.not_found", submission_id=submission_id)
            return
        if sub.status != "queued":
            log.info("form.submission.already_processed", submission_id=submission_id, status=sub.status)
            return

        await set_tenant_context(session, sub.tenant_id)

        tenant = await ctx["tenant_loader"].load_by_id(sub.tenant_id)
        lead = await session.get(Lead, sub.lead_id)

        forms_cfg = tenant.forms[sub.provider]
        treeflow_id = forms_cfg.start_treeflow

        # Create Talk pré-populado
        talk = await create_talk_with_state(
            session=session,
            tenant=tenant,
            lead=lead,
            treeflow_id=treeflow_id,
            preloaded_collected=sub.field_values,
        )

        # Send proactive HSM if configured
        if forms_cfg.proactive_first_message and forms_cfg.proactive_first_message.enabled:
            pfm = forms_cfg.proactive_first_message
            secrets = SopsLoader.load(tenant.slug)
            messaging = build_messaging_adapter(tenant.messaging, secrets)
            params = render_params(
                pfm.params,
                {"collected": sub.field_values, "lead": {"whatsapp_e164": lead.whatsapp_e164}}
            )
            try:
                result = await messaging.send_template(
                    to=lead.whatsapp_e164,
                    template_ref=pfm.template_ref,
                    language=pfm.language,
                    params=params,
                )
                # record_outbound (Plano 10 helper) com triggered_by='form_inbound'
                await record_outbound(session, talk.id, result, triggered_by="form_inbound")
                log.info("form.proactive_sent", lead_id=str(lead.id), talk_id=str(talk.id), external_id=result.external_id)
            except (PolicyError, AuthError) as exc:
                log.error("form.proactive_failed_terminal", lead_id=str(lead.id), err=str(exc))
                talk.status = "requires_review"
                talk.requires_review_reason = "proactive_hsm_failed"
            except RecipientUnreachable:
                lead.status = "unreachable"
                lead.unreachable_reason = "proactive_hsm_recipient_unreachable"
            except MessagingError as exc:
                log.warning("form.proactive_failed_transient", lead_id=str(lead.id), err=str(exc))
                raise  # arq retry

        sub.status = "processed"
        sub.processed_at = utcnow()
        await session.commit()
```

#### 8.2.7. `schemas/tenant_yaml.py` — adições

```python
# Existente: ScheduleConfig, ConversationConfig, GuardrailsConfig, MessagingConfig,
#            ObjectionsConfig, ConsoleConfig, LLMDefaults, ReengagementTemplate.
# Esta spec adiciona:

class ProactiveFirstMessageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    template_ref: str = Field(min_length=1)
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)


class FormProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    shared_secret_ref: str | None = None
    hmac_secret_ref: str | None = None
    start_treeflow: str = Field(min_length=1)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    proactive_first_message: ProactiveFirstMessageConfig | None = None

    @model_validator(mode="after")
    def _check_secret_ref(self):
        if self.enabled and not (self.shared_secret_ref or self.hmac_secret_ref):
            raise ValueError("forms.<provider>.enabled=true requires shared_secret_ref or hmac_secret_ref")
        return self


class RDStationCRMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token_ref: str
    client_id_ref: str
    client_secret_ref: str
    pipeline_id: str
    stage_mapping: dict[Literal["open", "won", "lost"], str]
    custom_field_mapping: dict[str, str] = Field(default_factory=dict)


class CRMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1)
    rdstation: RDStationCRMConfig | None = None
    # outros vendors entram aqui

    @model_validator(mode="after")
    def _check_provider_block_present(self):
        if not getattr(self, self.provider, None):
            raise ValueError(
                f"crm.provider={self.provider!r} requires crm.{self.provider}: {{...}} block"
            )
        return self


class TenantConfig(BaseModel):
    # ... campos existentes ...
    forms: dict[str, FormProviderConfig] = Field(default_factory=dict)
    crm: CRMConfig | None = None
```

#### 8.2.8. Migration 0030

```python
"""Form ingestion + crm refs in Lead

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Lead.crm_refs JSONB
    op.add_column("leads",
        sa.Column("crm_refs", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))
    )
    op.create_index("ix_leads_crm_refs_gin", "leads", ["crm_refs"], postgresql_using="gin")

    # 2. inbound_form_submissions
    op.create_table("inbound_form_submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL")),
        sa.Column("raw", JSONB(), nullable=False),
        sa.Column("field_values", JSONB(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error_detail", sa.Text()),
        sa.CheckConstraint("status IN ('queued', 'processed', 'skipped_dedupe', 'error')",
                          name="ck_inbound_form_status"),
    )
    op.create_index("uq_inbound_form_extid", "inbound_form_submissions",
                    ["tenant_id", "provider", "external_id"], unique=True)
    op.create_index("ix_inbound_form_lead_status", "inbound_form_submissions",
                    ["lead_id", "status"], postgresql_where=sa.text("status IN ('queued', 'error')"))

    # 3. RLS
    op.execute("ALTER TABLE inbound_form_submissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_form_submissions FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON inbound_form_submissions
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON inbound_form_submissions")
    op.drop_table("inbound_form_submissions")
    op.drop_index("ix_leads_crm_refs_gin", table_name="leads")
    op.drop_column("leads", "crm_refs")
```

### 8.3. Plano detalhado por tarefa (TDD por task — convenção do CLAUDE.md)

#### FASE A — Form ingestion (10 tarefas, ~1.5 semanas)

| Task | Escopo | Entregável | Critério de aceitação |
|---|---|---|---|
| **A1** | Migration `0030_form_ingestion_and_crm_refs.py` | DDL completo (crm_refs JSONB + inbound_form_submissions + RLS) | `make migrate` aplica sem erro; downgrade reverte limpo |
| **A2** | Schema tenant.yaml — `FormProviderConfig` + `ProactiveFirstMessageConfig` | Adições em `schemas/tenant_yaml.py` | Test unit: tenant.yaml com `forms.respondi` valida; sem `start_treeflow` falha |
| **A3** | Model + Repository `InboundFormSubmission` | ORM + `find_or_create`, `mark_processed`, `mark_error` | Test integration com RLS — tenant A não vê submissions de tenant B |
| **A4** | Subsistema `forms/`: base + registry + factory + errors | `FormProviderAdapter` ABC + `@register` + `build_form_adapter` | Test unit: registro dup-name falha; factory unknown-provider levanta |
| **A5** | `RespondiFormAdapter` + fixtures | Adapter completo + 2 fixtures JSON (text form + form com utms) | Test unit: parsing OK; signature inválida → SignatureError; phone normalizado pra E.164 |
| **A6** | `forms/ingest.py` — find_or_create_lead_by_form + create_talk_with_state | Helper que cria Lead + Talk + pre-popula TalkFlowState | Test integration: 2 chamadas com mesmo whatsapp_e164 → mesmo lead, talks distintas |
| **A7** | Route `POST /webhooks/{slug}/form/{provider}` | `api/routes/forms.py` + registro em `main.py` | Test integration: 404 sem tenant; 401 secret inválido; 200 + dedup em re-submit |
| **A8** | Worker job `process_form_inbound` | `worker/jobs/process_form_inbound.py` + registro em `worker/main.py` | Test integration com FakeMessagingAdapter: Talk criada + HSM disparado |
| **A9** | Test E2E form ingestion (mockando WhatsApp) | `test_messaging_form_e2e.py` similar ao existente | Submit fixture Respondi → Lead criado → Talk active → HSM "enviado" |
| **A10** | CLAUDE.md seção "Form ingestion (Plano 7a)" | Documentação operacional | Inclui: URL shape, como configurar Respondi, como rotacionar secret |

**Total Fase A:** 10 commits, ~3-5 dias de dev focado.

#### FASE B — CRM action adapter (12 tarefas, ~2 semanas)

| Task | Escopo | Entregável | Critério de aceitação |
|---|---|---|---|
| **B1** | Schema tenant.yaml — `CRMConfig` + `RDStationCRMConfig` | Adições em `schemas/tenant_yaml.py` | Test unit: `provider=rdstation` sem `rdstation:` block falha; stage_mapping incompleto falha |
| **B2** | Subsistema `flowengine/actions/crm/`: canonical + backend ABC + factory + errors | `ContactCanonical`, `DealCanonical`, `DealStage`, `CRMBackend`, `build_crm_backend` | Test unit: registry de backends; build falha com provider desconhecido |
| **B3** | `CRMActionAdapter` registrado como `name="crm"` | `adapter.py` + side-effect import em `actions/__init__.py` | Test unit: dispatch p/ create_or_update_contact; unknown handler levanta |
| **B4** | `lead.crm_refs` no template context do FE-03c | Modificação em `flowengine/actions/templating.py` (`build_template_context`) | Test unit: template `{{ lead.crm_refs.rdstation.contact_id }}` resolve OK |
| **B5** | `RDStationOAuth` — token + refresh | `rdstation/oauth.py` | Test unit (mock httpx): primeira call faz refresh; cache hit reusa token |
| **B6** | `RDStationClient` — HTTP layer + tenacity | `rdstation/client.py` com retry de 3x exponencial + error mapping (401/403/422/429/5xx) | Test unit (responses_mock): cada status code mapeia exception correta |
| **B7** | `RDStationCRMBackend.create_or_update_contact` | Implementação completa: lookup local → search remote → create | Test integration mock: 3 cenários (já em refs / encontra remoto / cria novo) |
| **B8** | `RDStationCRMBackend.create_or_update_deal` | Implementação completa + custom_field_mapping | Test mock: deal vinculado ao contact; product duplicado reusa |
| **B9** | `RDStationCRMBackend.update_deal_stage` + `record_qualification_note` | Handlers restantes | Test mock |
| **B10** | Persist refs em `Lead.crm_refs` — helper repository | Helper transacional UPSERT atomic | Test integration: 2 actions concorrentes pro mesmo lead não corrompem refs (advisory lock) |
| **B11** | Smoke test `tests/integration/test_rdstation_smoke.py` (gated) | Gated por `RDSTATION_SMOKE=1` env var, hits sandbox real | Documentado em CLAUDE.md como rodar |
| **B12** | CLAUDE.md seção "CRM (Plano 7b)" | Documentação operacional | Inclui: OAuth setup inicial, rotação de refresh_token, troubleshooting |

**Total Fase B:** 12 commits, ~7-10 dias de dev focado.

#### FASE C — Wiring na Manoela (5 tarefas, ~3 dias)

| Task | Escopo | Entregável | Critério de aceitação |
|---|---|---|---|
| **C1** | Obter credenciais RD Station Manoela | Refresh token via OAuth flow inicial (1x via curl/script) + pipeline_id + stage_ids | Operador conectou app no painel; refresh_token guardado |
| **C2** | Atualizar `tenants/manoela-mentora/tenant.yaml` | Add blocos `forms.respondi` + `crm.rdstation` com configs reais | TenantLoader carrega sem erro |
| **C3** | Adicionar secrets cifrados | `sops tenants/manoela-mentora/secrets.enc.yaml` editado → 4 chaves novas | `head -10` mostra ENC[...]; smoke load secrets passa |
| **C4** | Atualizar TreeFlow `qualificacao_inicial.yaml` v0.3.0 | Add `on_collected` com `adapter: crm` em `saudacao` (create_contact) e `qualificacao` (create_deal) | Schema valida; bump version pra v0.3.0 |
| **C5** | Smoke test E2E real | Preencher form de teste no painel Respondi → mensagem WhatsApp chega → CRM RD Station populado | Operador (Lana) confirma no painel RD Station |

**Total Fase C:** 5 commits, ~2-3 dias (espera por aprovação de templates HSM e config OAuth pode adicionar dias).

### 8.4. Ordem sugerida de PRs

Pra revisão incremental por Nicolas, sugiro **3 PRs separadas** (não monolítica):

| PR | Branch | Conteúdo | Tamanho aproximado |
|---|---|---|---|
| **PR-1** | `dev/pedro-fe-form-ingestion` | Tasks A1–A10 (Fase A inteira, sem CRM) | ~15 arquivos, ~1500 LOC |
| **PR-2** | `dev/pedro-fe-crm-rdstation` | Tasks B1–B12 (Fase B inteira) | ~20 arquivos, ~2000 LOC |
| **PR-3** | `dev/pedro-manoela-crm-wiring` | Tasks C1–C5 (Fase C) | ~3 arquivos (tenant.yaml, secrets, treeflow) |

Cada PR independente, mergeável separadamente. PR-2 depende de PR-1 (precisa de `Lead.crm_refs`).

### 8.5. Dependências externas (não-código)

| Item | Onde obter | Quando | Bloqueante de |
|---|---|---|---|
| Credenciais OAuth RD Station (client_id, client_secret) | Painel RD Station Developers — cliente Manoela cria app | Antes da Fase C | C2 |
| Refresh token RD Station | OAuth flow inicial (1x via script) | Antes da Fase C | C3 |
| Pipeline ID + stage IDs do RD Station | Painel RD Station — pipeline configurado pra Manoela | Antes da Fase C | C2 |
| Template HSM `saudacao_proativa_v1` aprovado no Meta | Meta Business Manager — submetido pela Manoela/Lana | Antes da Fase C | Smoke test C5 |
| URL pública do webhook (https) | Setup Traefik + DNS (item 1.4 da roadmap) | Antes da Fase C | Smoke test C5 |
| Form Respondi configurado com question_ids estáveis | Painel Respondi — Manoela cria form | Antes da Fase C | Smoke test C5 |

### 8.6. Fase D — Não inclusa nesta spec

Pra contexto / referência ao ADR. Acionar conforme gatilhos:

| Fase ADR | O quê | Gatilho |
|---|---|---|
| Fase 2 | Refresh on re-engagement (lead que volta, busca deals existentes) | Quando primeiro lead Manoela retornar pós-fechamento |
| Fase 3 | Tabelas Contact/Deal/Organization internas | 2-3 clientes OR dor real de sync no piloto |
| Fase 4 | Sync bidirecional (webhooks RD Station → PeSDR) | CRM interno em produção + cliente com CRM ativo |
| Fase 5 | Organizations + multi-stakeholder | Primeiro tenant B2B real |

---

## 9. Trade-offs explícitos

### 9.1. Por que ActionAdapter `crm` único + backends, e não 1 adapter por vendor

✅ Pro: TreeFlow YAML vendor-agnostic, troca de CRM = 1 linha no tenant.yaml
❌ Con: indireção extra (adapter → backend → vendor API), mais classes
**Veredito:** vale — escalabilidade pra outros clientes é prioridade alta no ICP

### 9.2. Por que NÃO criar tabelas Contact/Deal/Org agora

✅ Pro: muito menos código (4 migrations + 4 ORM + 4 repositórios + UI HITL adaptada economizados); ADR explicitamente puxa pra Fase 3
❌ Con: queries futuras tipo "todos os deals do tenant com stage=open" exigem aggregate em `Lead.crm_refs` JSONB (mais lento que tabelas relacionais)
**Veredito:** OK pro piloto Manoela — não vai ter volume + relatório agora. Fase 3 quando dor real aparecer.

### 9.3. Por que FormProviderAdapter como nova borda, e não reuse de MessagingAdapter

✅ Pro: contract dedicado (`handle_submission` retorna `IngestedFormSubmission` com campos estruturados), sem forçar tudo virar mensagem
❌ Con: novo subsistema, mais código pra manter
**Veredito:** semantic clarity > code reuse. Form e mensagem são entradas conceptualmente diferentes.

### 9.4. Por que pré-popular `collected` no TalkFlowState (em vez de só inbound_payload separado)

✅ Pro: TreeFlow autores não precisam aprender 2 fontes de dados; agente trata campo do form igual a campo da conversa
❌ Con: precisa marcar quais foram pré-coletados pra system prompt do node entender que não precisa perguntar de novo (resolve via prompt engineering)
**Veredito:** decidir no Plano 7 execution — testar 2 abordagens em prompt e ver qual o LLM lida melhor

### 9.5. Por que primeira mensagem proativa via HSM, e não tentar mensagem livre

❌ Pro: meta API só aceita HSM pra primeira mensagem fora de janela 24h. Não é decisão nossa, é restrição da plataforma.
**Veredito:** decisão tomada pela Meta; só refletindo a realidade

### 9.6. Por que shared_secret na URL pro Respondi (não HMAC)

✅ Pro: Respondi não suporta HMAC nativo; alternativa seria gambiarra (header custom que Respondi não envia)
❌ Con: secret na URL aparece em logs de proxy / browser history se vazado
**Mitigação:**
- URL nunca é exposta em frontend (configurada no painel Respondi)
- Rotacionar shared_secret periodicamente (manual via `sops`)
- Se Respondi adicionar HMAC no futuro, migrar facilmente (mesma classe, troca de método de validação)

---

## 10. Riscos e mitigações

| Risco | Severidade | Mitigação |
|---|---|---|
| RD Station rotaciona refresh_token e PeSDR não atualiza secrets.enc.yaml | Alta (corta CRM sync silenciosamente) | Alert estruturado + worker fail terminal; operador atualiza manual. Plano futuro: `crm_tokens` table |
| Respondi muda shape do payload (sem aviso) | Média | `field_mapping` por question_id (estável) > question_title (mutável); RespondiFormAdapter testes com fixtures versionadas |
| Lead matching errado (phone duplicado entre tenants) | Alta (LGPD!) | Lookup escopado por `tenant_id` + RLS; phone E.164 normalizado consistentemente |
| Talk pré-populado com `collected` engana o LLM (acha que coletou na conversa, age estranho) | Média | Test cobertura no smoke; ajustar prompt do node entry pra mencionar "campos vieram do formulário, cumprimente direto pelo nome" |
| RD Station rate limit (não documentado, mas existe) | Média | Tenacity backoff exponencial 3x; se persistente, action fica `failed`, alert |
| `on_collected` `adapter: crm` em tenant sem `crm:` block | Baixa | Loader emite warning load-time; dispatcher loga `crm_action.no_config` e skipa |
| Custom fields RD Station IDs diferentes entre tenants | Baixa | `tenant.crm.rdstation.custom_field_mapping` resolve por tenant |
| Worker crash mid-action_execute do CRM (action fica `executing` indefinido) | Média | FE-03c já documenta — adapter `must` be idempotent. Backends já são (lookup → upsert) |
| HSM template não aprovado no Meta (proactive_first_message falha) | Alta | Talk vai pra `requires_review`, operadora vê no console + alerta no log; submission marcada processed |
| Operadora cria tenant.yaml com forms ativo mas TreeFlow não tem on_collected: crm | Baixa | Não bloqueia — só não escreve no CRM. Loader warning ajuda autor |

---

## 11. Open questions pro Nicolas

Decisões que dependem de input/preferência dele antes de virar plano executável:

1. **Localização do CRM subsistema:** `src/ai_sdr/flowengine/actions/crm/` (proposto) OR `src/ai_sdr/crm/` no top-level (mais visível, deixa clear que vai crescer pra Fase 3)?
2. **Pre-populate `collected` no TalkFlowState do Talk criado por form, OR campo novo `inbound_payload` ao lado:** segundo a sua leitura do FlowEngine v2, qual encaixa melhor com `apply_state_delta` e `evaluate_completion_rule`?
3. **`lead.crm_refs` no template context do FE-03c:** OK em adicionar (mudança trivial em `build_template_context`) ou prefere outra rota pra o backend ler refs?
4. **`crm.provider` = null / ausente:** TreeFlow YAML continua válido (action skipa) OR validador rejeita (mais conservador)?
5. **OAuth refresh_token rotation:** alert+manual update (proposta) OR investir em `crm_tokens` table com persistência DB já agora?
6. **`process_form_inbound` worker job:** mesmo file do `process_lead_inbox` (mantém worker enxuto) OR arquivo separado (clareza de domínio)?
7. **Multi-form num único tenant:** suportado por config (`forms.respondi` + `forms.typeform` no mesmo tenant)? Faz sentido pro ICP ou YAGNI?
8. **HSM proactive_first_message:** se template não está aprovado, Talk vai pra `requires_review`. Plano 9 tem padrão diferente de tratamento?
9. **Plano 6 (IdentityResolver):** `find_or_create_lead_by_form` agora ou esperar Plano 6 unificar com `find_or_create_lead_by_address`?
10. **TreeFlow YAML formato dos `on_collected` actions:** o exemplo da §4.8 fica natural ou prefere alguma sintaxe alternativa?
11. **Versão do TreeFlow Manoela:** bump pra v0.3.0 (`feat`) ou v0.2.2 (`fix`)?
12. **Smoke test em produção:** ambiente sandbox de RD Station pra Fase C, ou rodar contra prod direto com lead de teste?

---

## 12. Não-objetivos (fora de escopo)

**Esta spec NÃO cobre:**

| Item | Onde vai |
|---|---|
| Tabelas Contact, Deal, Organization no PeSDR | ADR Fase 3 — plano futuro |
| Sync bidirecional (webhooks RD Station → PeSDR) | ADR Fase 4 |
| Reconciliação noturna PeSDR ↔ CRM externo | ADR Fase 4 |
| Multi-stakeholder (2 sócios = 2 Leads na mesma Org) | ADR Fase 5 |
| Upsell flow (lead volta, busca deals existentes) | ADR Fase 2 — opcional adicionar à Fase B desta spec se sobrar tempo |
| UI no console HITL pra ver/editar Contact/Deal | Junto com Fase 3 do ADR |
| Custom field auto-creation no CRM externo | Plano dedicado de produção |
| HubSpot, Pipedrive, Kommo backends | Plano futuro (estrutura permite) |
| Typeform, Tally, Google Forms adapters | Plano futuro (estrutura permite) |
| Templates HSM em vários idiomas | Plano futuro |
| A/B test de `proactive_first_message` template | V2 do produto |
| Voice channels do FE-05 | FE-05 |
| Actions além de `on_collected` (`on_node_enter`, `on_close`, etc) | FE-04+ se necessário |
| `talk.handling_mode = auto_with_approval` interagindo com CRM actions | Plano 11 evolução |

---

## Referências

- [`2026-06-12-crm-posture-decision.md`](./2026-06-12-crm-posture-decision.md) — ADR macro CRM (esta spec implementa Fase 1)
- [`2026-06-12-fe03c-actions-adapter-framework-design.md`](./2026-06-12-fe03c-actions-adapter-framework-design.md) — Framework reutilizado pra CRM out
- [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) — Pattern macro
- [`2026-05-24-messaging-adapter-design.md`](./2026-05-24-messaging-adapter-design.md) — Pattern de referência pra FormProviderAdapter
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) §7 (entrada lead), §8 (sync CRM)
- RD Station CRM API: https://developers.rdstation.com/reference/instru%C3%A7%C3%B5es-e-requisitos
- RD Station OAuth: https://developers.rdstation.com/reference/autoriza%C3%A7%C3%A3o
- Respondi Webhook payload: https://help.respondi.app/article/48-webhooks-payload-de-exemplo
- Respondi webhook setup: https://help.respondi.app/article/43-como-fazer-integracoes-com-ferramentas-externas-usando-um-webhooks

---

**Fim da spec. Aguardando review do Nicolas antes de virar plan executável (skill `writing-plans`).**
