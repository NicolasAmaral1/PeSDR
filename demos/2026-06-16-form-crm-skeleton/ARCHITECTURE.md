# Arquitetura — resumo executivo

> Resumo das 5 decisões centrais da [spec](../../docs/superpowers/specs/2026-06-16-form-ingestion-and-crm-write-only-design.md). Pra detalhes, ler a spec inteira.

## 1. CRM out via ActionAdapter genérico `crm` + backends por vendor

Existem 2 caminhos pra suportar múltiplos CRMs:

| Opção | Pró | Con |
|---|---|---|
| **N adapters por vendor** (`rdstation`, `hubspot`, ...) | Simples | TreeFlow YAML acopla ao vendor; trocar CRM = reescrever YAMLs |
| **1 adapter genérico `crm` + backends** ✅ | Vendor-agnostic; trocar CRM = 1 linha em tenant.yaml | Indireção extra (adapter → backend) |

Escolhemos o segundo. O `CRMActionAdapter` (registrado no FE-03c registry com `name="crm"`) lê `tenant.yaml > crm.provider` em runtime e despacha pro backend correto via `build_crm_backend(provider, tenant, secrets)`.

```
TreeFlow YAML (vendor-agnostic):
  on_collected:
    - field: nome
      adapter: crm                            ← genérico
      handler: create_or_update_contact

CRMActionAdapter (FE-03c):
  ├─ lê tenant.crm.provider → "rdstation"
  └─ delega pra RDStationCRMBackend.create_or_update_contact(...)
```

**Handlers padronizados (vocabulário canônico do ADR CRM):**
- `create_or_update_contact`
- `create_or_update_deal`
- `update_deal_stage`
- `record_qualification_note`

Cada backend implementa esses handlers chamando a API do vendor.

## 2. Form ingestion como 4ª borda nova

PeSDR já tem 3 bordas com ABC + factory + registry: Messaging, HITL, Action (FE-03c). CRM é a 5ª (via ADR de 2026-06-12). Formulário é a 6ª.

Por que não reutilizar `MessagingAdapter`: a semântica é diferente. Mensagem é `{text, from, timestamp}`. Form é `{form_id, respondent_id, answers: dict, utms, score}`. Forçar virar mensagem perderia metadata e acoplaria conceitos distintos.

Novo subsistema `src/ai_sdr/forms/` espelha estrutura do `messaging/`:

```
forms/
├── base.py                   FormProviderAdapter ABC + IngestedFormSubmission dataclass
├── registry.py               FORM_PROVIDERS + @register decorator
├── factory.py                build_form_adapter
├── errors.py                 SignatureError, MalformedPayload, etc
├── ingest.py                 find_or_create_lead_by_form + create_talk_with_state
└── respondi.py               RespondiFormAdapter (primeiro impl)
```

Contract base:

```python
class FormProviderAdapter(ABC):
    name: str
    @abstractmethod
    async def handle_submission(
        self, raw_body: bytes, headers, query_params
    ) -> IngestedFormSubmission: ...
```

## 3. Lead.crm_refs JSONB armazena IDs externos

Nova coluna em `leads`:

```python
lead.crm_refs = {
    "rdstation": {
        "contact_id": "abc123",
        "deal_id_mentoria": "def456",
        "deal_id_aceleradora": null,
        "last_synced_at": "2026-06-16T..."
    }
}
```

**Idempotência multi-camada:**
1. **FE-03c dispatcher:** UNIQUE `(talk, field, value_hash)` — mesmo turno, mesmo valor = no-op
2. **Backend lookup local:** lê `Lead.crm_refs.rdstation.contact_id` antes de criar
3. **Backend lookup remoto:** search por phone no CRM externo (cliente já existe lá antes)
4. **Worker arq retry:** seguro porque os 3 níveis acima cobrem

**Por que JSONB e não tabelas Contact/Deal/Organization separadas:**
- ADR CRM puxa tabelas pra Fase 3 (depois de 2-3 clientes OR dor real de sync)
- Custo de 4 migrations + ORM + repositórios + UI HITL adaptada economizado agora
- JSONB cobre 100% das necessidades do piloto Manoela

## 4. Pre-populate `collected` do TalkFlowState com campos do form

Quando o form entrega `{nome: "Maria", faturamento_mensal: 40000}`, o `TalkFlowState` do Talk recém-criado nasce com:

```python
talk.state.collected = {"nome": "Maria", "faturamento_mensal": 40000}
talk.state.current_node = "saudacao"  # entry_node
```

O FlowEngine v2 vê os campos como já preenchidos. O LLM no `saudacao` cumprimenta pelo nome direto, em vez de perguntar.

**Mapeamento explícito por tenant** em `tenant.yaml > forms.respondi.field_mapping`:

```yaml
forms:
  respondi:
    field_mapping:
      qst_abc123: nome                  # form question_id → collected field
      qst_def456: whatsapp_e164         # vai pra LeadIdentifier, não collected
      qst_ghi789: faturamento_mensal
```

## 5. Primeira mensagem proativa via template HSM (Plano 9)

Lead chega via form → fora da janela de 24h do WhatsApp → não dá pra enviar mensagem livre. Tem que ser **template HSM aprovado pela Meta**.

Plano 9 já entrega `send_template` no `WhatsAppCloudAdapter`. Esta spec só conecta: o worker `process_form_inbound` chama `messaging.send_template(...)` usando `tenant.yaml > forms.respondi.proactive_first_message.template_ref`.

```yaml
forms:
  respondi:
    proactive_first_message:
      enabled: true
      template_ref: "saudacao_proativa_v1"      # HSM aprovado no Meta Business Manager
      language: pt_BR
      params:
        - "{{ collected.nome | default('') }}"
```

## Diagrama de fluxo E2E

```
T0 ─ Lead preenche form Respondi
       ↓
T1 ─ POST /webhooks/manoela-mentora/form/respondi?secret=...
       │ verifica secret
       │ parseia payload (raw_answers → field_values)
       │ find_or_create_lead_by_form (por whatsapp_e164)
       │ INSERT inbound_form_submissions ON CONFLICT DO NOTHING
       │ enqueue process_form_inbound
       ↓
T2 ─ Worker: process_form_inbound
       │ Talk criada com TalkFlowState.collected = field_values
       │ envia template HSM via messaging_adapter.send_template
       │ Talk fica em status=active
       ↓
T3 ─ Lead responde no WhatsApp
       ↓
T4 ─ POST /webhooks/manoela-mentora/whatsapp_cloud (existente, Plano 5)
       │ INSERT inbound_messages
       │ enqueue process_lead_inbox
       ↓
T5 ─ Worker: process_lead_inbox → run_turn (FlowEngine v2)
       │ LLM extrai campos
       │ dispatch_actions (FE-03c)
       │ pra cada on_collected do node atual:
       │   render Jinja2 params
       │   INSERT action_executions ON CONFLICT DO NOTHING
       │   enqueue execute_action
       │ envia resposta via messaging.send_text
       ↓
T6 ─ Worker: execute_action (FE-03c)
       │ build_action_adapter("crm", tenant) → CRMActionAdapter
       │ CRMActionAdapter despacha pro RDStationCRMBackend
       │ Backend: lookup local Lead.crm_refs → search remoto → create
       │ UPDATE Lead.crm_refs.rdstation.contact_id
       │ mark action_execution status=success
```

## Bordas finais do PeSDR (pós esta spec)

| Borda | ABC | Default impl | Status |
|---|---|---|---|
| Messaging | `MessagingAdapter` | `WhatsAppCloudAdapter` | Existe (Plano 5) |
| Identity | `IdentityResolver` | `InternalLead` | Plano 6 (futuro) |
| HITL | `HitlSink` | Console Plano 11 | Existe |
| Action | `ActionAdapter` | `LoggingActionAdapter`, **CRMActionAdapter (novo)** | Existe (FE-03c) |
| **Form** ⭐ | `FormProviderAdapter` | `RespondiFormAdapter` | **Proposto aqui** |

5 bordas no total. Todas seguem o mesmo pattern (ABC + registry + factory + tenant.yaml config).
