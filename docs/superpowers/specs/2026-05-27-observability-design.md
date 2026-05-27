# Spec: Observability — LangSmith + outbound audit (Plano 10)

**Data:** 2026-05-27
**Status:** Aceito (brainstorm fechado, pronto pra plano)
**Autor:** Nicolas Amaral (decisão com Claude)
**Referências:**
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) — spec master
- [`2026-05-24-messaging-adapter-design.md`](./2026-05-24-messaging-adapter-design.md) — Plano 5 (`MessagingAdapter`, `inbound_messages`, error taxonomy)
- [`2026-05-27-followup-and-hsm-design.md`](./2026-05-27-followup-and-hsm-design.md) — Plano 9 (`follow_up_jobs`, scanner, `send_template`)

---

## 1. Contexto

Após Planos 1–5 e 9, o PeSDR processa mensagens via WhatsApp Cloud, roda agente com LLM via langchain/LangGraph, dispara templates HSM agendados e tem guardrails que retentam fallos. **Mas não tem visibility sistemática.** Hoje:

- Logs estruturados (structlog) já existem, com eventos pontuais como `wa.send.start`, `worker.ready`, `webhook.signature_error`. Suficiente pra debug ad-hoc, ruim pra análise agregada.
- LLM calls não têm traces estruturados — pra debugar "por que o agente respondeu X em Y turn", o operador grep'a logs.
- Mensagens enviadas pelo agente **não são persistidas** — adapter retorna `SendResult.external_id` e a info se perde. Não tem como reconstruir o que o agente disse a um lead específico.

**Plano 10 adiciona 2 mecanismos complementares:**

1. **LangSmith tracing** auto-ativado nas chains langchain/LangGraph existentes via env vars + metadata padronizada por trace (tenant_slug, lead_id, talkflow_id, current_node, turn_index, trace_origin). Cobertura: cada LLM call vira um trace navegável com prompt, completion, latency, custo, hierarquia de chains.

2. **Tabela `outbound_messages`** que persiste cada mensagem enviada pelo agente (text + template, success + failure), com causa (`triggered_by` enum: inbound, follow_up_scanner, window_expired_recovery), FKs frouxas pra `inbound_messages.id` ou `follow_up_jobs.id`, e payload renderizado de templates. Foundation pra: CLI de auditoria, futura conversation viewer (P11b), análise pós-incidente.

Esses dois pedaços cobrem o "D" do trade-off de Q1 (LangSmith + outbound_messages, sem Prometheus/Grafana/OTel) — adequado pro piloto Joana com 1 cliente.

---

## 2. Decisão (síntese)

PeSDR ganha:

- **LangSmith tracing opt-in** via 3 env vars (`LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY`, `LANGCHAIN_PROJECT`). Desligado por default em dev local.
- **Helper `build_trace_metadata`** em `src/ai_sdr/observability/tracing.py` que produz dict padronizado (tenant_slug, talkflow_id, lead_id, node, turn_index, trace_origin) pra anexar a cada `ainvoke(messages, config={"metadata": ...})`.
- **4 call sites de LLM ganham metadata explícita** — runtime graph, objection classifier, field extractor, guardrails critic.
- **Tabela `outbound_messages`** (tenant-scoped RLS, migration 0011) — audit completo de send_text e send_template.
- **Helpers `record_outbound_sent` / `record_outbound_failed`** em `src/ai_sdr/observability/outbound_audit.py` — chamados pelo worker (P5 + P9 paths) e pelo scanner (P9 path) após cada adapter call.
- **`OutboundMessage` ORM** + `__init__.py` re-export.
- **CLI `ai-sdr outbound list --tenant <slug> [--lead <uuid>] [--status sent|failed|all] [--limit N]`** — tabela rich, audit-friendly.
- **Startup validation** em `main.py` — warning se `LANGCHAIN_TRACING_V2=true` mas `LANGSMITH_API_KEY` ausente (não trava o app).

---

## 3. Não-objetivos

- **Sem Prometheus / Grafana / OTel** — decidido em Q1 ("D"). Quando volume justificar, plano dedicado de "Observability scale-up" introduz.
- **Sem alertas / pagerduty / Slack webhook** — log estruturado serve por enquanto. Plano dedicado pós-MVP.
- **Sem dashboard web no PeSDR** — LangSmith UI cobre traces; CLI cobre audit. Conversation viewer (joins inbound + outbound) é P11b.
- **Sem cost tracking customizado** — LangSmith reporta tokens/custo nativamente. Sem duplicar no nosso DB.
- **Sem traces de DB queries / arq jobs em si** — só LLM calls. OTel-style spans em `process_lead_inbox` / scanner ficam pra plano futuro.
- **Sem sampling configurável** — 100% em v1. Quando bater limite do free tier, plano dedicado pode adicionar `LANGCHAIN_SAMPLE_RATE`.
- **Sem retry/outbox-pattern pra audit row em caso de DB commit failure** — race conhecida (mensagem enviada, audit não persistida) é aceita; warning log + investigação via LangSmith trace é o workaround. 2-phase commit fica pra plano dedicado.
- **Sem rate-limit anti-burst no LangSmith** — free tier do LangSmith aguenta; lib não tem mecanismo nativo. Plano dedicado se precisar.
- **Sem audit das mensagens recebidas (inbound)** — `inbound_messages` (P5) já cobre. P10 só adiciona o lado outbound, simétrico.

---

## 4. Arquitetura

```
┌─────────────────────────────────────────────────────────────────────────┐
│  LLM CALLS (4 sites, todos via langchain)                                │
│  ┌──────────────────┐   ┌──────────────────────┐   ┌─────────────────┐  │
│  │ runtime.graph    │   │ classifier.structured│   │ critic.runnable │  │
│  │ .ainvoke(state,  │   │ .ainvoke(messages,   │   │ .ainvoke(msgs,  │  │
│  │  config={meta})  │   │  config={meta})      │   │  config={meta}) │  │
│  └────────┬─────────┘   └──────────┬───────────┘   └────────┬────────┘  │
│           │                        │                        │           │
│           ▼                        ▼                        ▼           │
│  ┌──────────────────┐                                                    │
│  │ extractor.runn   │   build_trace_metadata({tenant_slug, lead_id,      │
│  │ .ainvoke(msgs,   │     talkflow_id, node, turn_index, trace_origin})  │
│  │  config={meta})  │                                                    │
│  └─────────┬────────┘                                                    │
│            │                                                             │
│            └─── auto-captured by langchain-core ───┐                     │
└────────────────────────────────────────────────────┼─────────────────────┘
                                                    ▼
                                            ┌───────────────────┐
                                            │  LangSmith Cloud  │
                                            │  project: pesdr-* │
                                            └───────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  ADAPTER SENDS (3 paths)                                                 │
│  ┌──────────────────────┐                                                │
│  │ process_lead_inbox   │── send_text ─► returns SendResult              │
│  │ (P5 worker)          │             ─► raises TerminalError            │
│  └──────────┬───────────┘                                                │
│             │                                                            │
│  ┌──────────▼───────────┐                                                │
│  │ window_expired       │── send_template ─► returns / raises             │
│  │ recovery (P9)        │                                                │
│  └──────────┬───────────┘                                                │
│             │                                                            │
│  ┌──────────▼───────────┐                                                │
│  │ follow_up_scanner    │── send_template ─► returns / raises             │
│  │ ._fire_follow_up (P9)│                                                │
│  └──────────┬───────────┘                                                │
│             │                                                            │
│             └─── on success ─► record_outbound_sent(...)                 │
│             └─── on failure ─► record_outbound_failed(...)                │
│                                  ▲                                       │
│                                  │ INSERT                                │
│                                  ▼                                       │
└──────────────────────────  outbound_messages table (RLS)  ───────────────┘
                                  ▲
                                  │ SELECT
                                  │
┌─────────────────────────────────┴────────────────────────────────────────┐
│  ai-sdr outbound list                                                    │
│    --tenant <slug> [--lead <uuid>] [--status sent|failed|all] [--limit]  │
└──────────────────────────────────────────────────────────────────────────┘
```

**Princípios:**

1. **LangSmith é opt-in via env** — zero overhead se desligado. Lib auto-traceia sem mudar lógica.
2. **Metadata é tag, não payload** — não duplicamos lead_id/talkflow_id no trace body; só em `RunnableConfig.metadata` pra filtro no dashboard.
3. **`outbound_messages` é write-only do app** — só worker/scanner inserem. CLI/UI futuras só leem.
4. **Audit captura SUCCESS + FAILURE** — falhas são parte do registro, não exceção.
5. **`triggered_by` é narrativo** — cada row sabe se veio de inbound, follow_up_scanner, ou window_expired_recovery. Permite reconstruir o "porquê" deste send via SQL.
6. **Helpers no boundary** (`observability/`) — call sites importam funções, não duplicam INSERT logic. Worker e scanner usam os mesmos helpers.

---

## 5. Modelo de dados

### Migration `0011_outbound_messages.py`

```sql
CREATE TABLE outbound_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    talkflow_id UUID NOT NULL REFERENCES talkflows(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

    provider TEXT NOT NULL,
    message_type TEXT NOT NULL,

    body_text TEXT,
    template_ref TEXT,
    template_language TEXT,
    template_params JSONB,

    status TEXT NOT NULL,
    external_id TEXT,
    error_detail TEXT,

    triggered_by TEXT NOT NULL,
    inbound_message_id UUID REFERENCES inbound_messages(id) ON DELETE SET NULL,
    follow_up_job_id UUID REFERENCES follow_up_jobs(id) ON DELETE SET NULL,

    sent_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_outbound_message_type CHECK (message_type IN ('text', 'template')),
    CONSTRAINT ck_outbound_status CHECK (status IN ('sent', 'failed')),
    CONSTRAINT ck_outbound_triggered_by CHECK (
        triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery')
    ),
    CONSTRAINT ck_outbound_body_consistency CHECK (
        (message_type = 'text' AND body_text IS NOT NULL AND template_ref IS NULL)
        OR
        (message_type = 'template' AND template_ref IS NOT NULL AND body_text IS NULL)
    )
);

CREATE INDEX ix_outbound_messages_lead_sent
    ON outbound_messages (lead_id, sent_at DESC);
CREATE INDEX ix_outbound_messages_tenant_sent
    ON outbound_messages (tenant_id, sent_at DESC);

ALTER TABLE outbound_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY outbound_messages_tenant_isolation ON outbound_messages
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

**Notas de design:**

- **Check constraint XOR** (`ck_outbound_body_consistency`) força: text tem body_text e NÃO tem template_ref; template tem template_ref e NÃO tem body_text. Schema garante consistência sem validador Python.
- **`triggered_by` enum estreito** — 3 valores cobrem todos os paths v1. Adicionar futuro (e.g., `'manual_takeover'` quando P11d aterrissar) = 1 migration de alter check constraint.
- **FKs frouxas em `inbound_message_id` + `follow_up_job_id`** (`ON DELETE SET NULL`) — cleanup futuro de inbound/follow_up_jobs não quebra audit. Audit perde rastreio causal mas mantém histórico do send em si.
- **`template_params JSONB`** — guarda o array já renderizado (Jinja → strings). Audit-friendly: vê exatamente o que foi pra Meta.
- **Indexes**: `(lead_id, sent_at DESC)` cobre histórico-por-lead (uso primário do conversation viewer futuro); `(tenant_id, sent_at DESC)` cobre listagem do CLI.

### `OutboundMessage` ORM

```python
# src/ai_sdr/models/outbound_message.py

class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    talkflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("talkflows.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    message_type: Mapped[str] = mapped_column(Text(), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_language: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_params: Mapped[list[str] | None] = mapped_column(JSONB(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    triggered_by: Mapped[str] = mapped_column(Text(), nullable=False)
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbound_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    follow_up_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("follow_up_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

---

## 6. LangSmith setup + metadata tagging

### Env vars

```bash
LANGCHAIN_TRACING_V2=true                  # liga tracing global (langchain-core lê)
LANGSMITH_API_KEY=ls__...                  # https://smith.langchain.com → API Keys
LANGCHAIN_PROJECT=pesdr-prod               # ou pesdr-dev local
```

Sem `LANGCHAIN_TRACING_V2=true` → langchain skip-a o envio. Zero overhead.
Com flag mas sem key → langchain falha silencioso. Por isso o startup validator do `main.py` emite warning logo de cara.

### Settings

```python
# src/ai_sdr/settings.py
class Settings(BaseSettings):
    # ... existing ...

    langchain_tracing_v2: bool = False
    langsmith_api_key: str | None = None
    langchain_project: str = "pesdr-dev"
```

Os nomes batem com env vars que langchain consome direto — o Settings serve só pro startup validator.

### `build_trace_metadata` helper

```python
# src/ai_sdr/observability/tracing.py

def build_trace_metadata(
    *,
    tenant: Tenant | None = None,
    talkflow: TalkFlow | None = None,
    lead: Lead | None = None,
    node: str | None = None,
    turn_index: int | None = None,
    trace_origin: Literal[
        "process_lead_inbox",
        "follow_up_scanner",
        "window_expired_recovery",
        "simulate",
        "objection_classifier",
        "guardrails_critic",
        "field_extractor",
    ],
) -> dict[str, Any]:
    """Build the metadata dict to attach to a langchain ainvoke call.

    Each call site passes whatever it knows; missing fields just don't
    appear in the trace metadata. trace_origin is required so every
    trace is filterable by where it came from.
    """
    metadata: dict[str, Any] = {"trace_origin": trace_origin}
    if tenant is not None:
        metadata["tenant_id"] = str(tenant.id)
        metadata["tenant_slug"] = tenant.slug
    if talkflow is not None:
        metadata["talkflow_id"] = str(talkflow.id)
    if lead is not None:
        metadata["lead_id"] = str(lead.id)
    if node is not None:
        metadata["node"] = node
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    return metadata
```

### Call sites

| Arquivo | LLM call | trace_origin |
|---|---|---|
| `src/ai_sdr/treeflow/runtime.py:234` | `graph.ainvoke(input_state, config=cfg)` | `process_lead_inbox` (pega contexto do call site) |
| `src/ai_sdr/treeflow/classifier.py:84` | `structured.ainvoke(messages)` | `objection_classifier` |
| `src/ai_sdr/llm/extractor.py:82` | `runnable.ainvoke(messages)` | `field_extractor` |
| `src/ai_sdr/guardrails/critic.py:96` | `runnable.ainvoke(messages)` | `guardrails_critic` |

Pattern de uso:

```python
result = await runnable.ainvoke(
    messages,
    config={"metadata": build_trace_metadata(
        tenant=tenant, talkflow=talkflow, lead=lead,
        trace_origin="guardrails_critic",
    )},
)
```

Sub-traces (classifier/extractor/critic dentro do graph) **herdam metadata do parent automaticamente** (langchain core merge-a contexts). O `trace_origin` local ainda ajuda filtro direto no dashboard (`metadata.trace_origin = "guardrails_critic"`).

### Trace origins não-cobertos

`follow_up_scanner` e `window_expired_recovery` **não fazem LLM calls** — só chamam `adapter.send_template`. Sem traces LangSmith nesses paths. Audit é via `outbound_messages` (single source of truth desses eventos).

Pra adicionar traces customizados de NÃO-LLM ops no futuro (e.g., `@traceable` decorator em funções Python), plano dedicado de Observability scale-up.

### Sampling

100% em v1. Free tier do LangSmith (5k traces/mês) cobre piloto.

---

## 7. Outbound audit helpers

### `record_outbound_sent` + `record_outbound_failed`

```python
# src/ai_sdr/observability/outbound_audit.py

async def record_outbound_sent(
    session: AsyncSession,
    *,
    tenant: Tenant,
    talkflow: TalkFlow,
    lead: Lead,
    provider: str,
    message_type: Literal["text", "template"],
    triggered_by: Literal["inbound", "follow_up_scanner", "window_expired_recovery"],
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    external_id: str | None = None,
    sent_at: datetime,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a successful send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=talkflow.id, lead_id=lead.id,
        provider=provider, message_type=message_type,
        body_text=body_text,
        template_ref=template_ref, template_language=template_language,
        template_params=template_params,
        status="sent", external_id=external_id,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row


async def record_outbound_failed(
    session: AsyncSession,
    *,
    tenant: Tenant,
    talkflow: TalkFlow,
    lead: Lead,
    provider: str,
    message_type: Literal["text", "template"],
    triggered_by: Literal["inbound", "follow_up_scanner", "window_expired_recovery"],
    error_detail: str,
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    sent_at: datetime,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a failed send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=talkflow.id, lead_id=lead.id,
        provider=provider, message_type=message_type,
        body_text=body_text,
        template_ref=template_ref, template_language=template_language,
        template_params=template_params,
        status="failed", error_detail=error_detail,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row
```

### Audit write sites

| Onde | Quando | message_type | triggered_by | FK |
|---|---|---|---|---|
| `process_lead_inbox._process_one` | send_text success | `text` | `inbound` | `inbound_message_id=msg.id` |
| `process_lead_inbox._process_one` | send_text failure (4 except branches) | `text` | `inbound` | `inbound_message_id=msg.id` |
| `process_lead_inbox._process_one` | WindowExpired recovery send_template success | `template` | `window_expired_recovery` | `inbound_message_id=msg.id` |
| `process_lead_inbox._process_one` | WindowExpired recovery send_template failure | `template` | `window_expired_recovery` | `inbound_message_id=msg.id` |
| `follow_up_scanner._fire_follow_up` | send_template success | `template` | `follow_up_scanner` | `follow_up_job_id=job.id` |
| `follow_up_scanner._fire_follow_up` | send_template failure (3 except branches) | `template` | `follow_up_scanner` | `follow_up_job_id=job.id` |

Cada call site faz 1 INSERT antes do commit final da transação. Commit do call site engloba: `msg.status` update + audit row + qualquer follow_up_job mutation.

### Race conhecida: send sucedeu mas commit falhou

Se `adapter.send_*` retorna SUCCESS mas o `db.commit()` subsequente falha (DB down, RLS violation, etc.) → mensagem foi pra Meta mas DB não persiste o audit row. **Não retry-amos** porque o `external_id` da Meta já foi gravado lá; um retry causaria double-send.

**Mitigação v1**: o `except` em torno do commit emite `log.warning("outbound.audit_lost", external_id=..., reason=...)` com payload suficiente pra reconstruir o evento. Operador investiga via LangSmith trace (que chegou independente do nosso DB).

Plano futuro: outbox-pattern com 2-phase commit (insert audit row antes do send, mark `pending`; após send, update pra `sent`/`failed`). Fora de escopo v1.

---

## 8. CLI ops

```bash
ai-sdr outbound list --tenant <slug> [--lead <uuid>] [--status sent|failed|all] [--limit N]
```

Output (rich table):

| Sent At | Type | Lead | Trigger | Status | Content / Template | External ID |
|---|---|---|---|---|---|---|
| 14:32:18 | text | +55 11 988…7777 | inbound | sent | "Olá! Sou a Joana…" (trunc 60) | wamid.X… |
| 14:35:01 | template | leila-teste | follow_up_scanner | sent | followup_24h_v1 ["amigo"] | wamid.Y… |
| 14:36:44 | text | +55 11 977…6666 | inbound | failed | "Como posso ajudar?" | RecipientUnreachable… |

**Filtros:**
- `--lead <uuid>` (opcional) — histórico de 1 lead
- `--status sent|failed|all` — default `all`
- `--limit N` — default 50, mais recentes primeiro
- ORDER BY `sent_at DESC`

Implementação em `src/ai_sdr/cli/outbound.py`. Mesma pattern de session-via-engine dos outros CLI (simulate/users/follow_ups).

---

## 9. Module layout

```
src/ai_sdr/
├── observability/                          # NEW package
│   ├── __init__.py
│   ├── tracing.py                          # NEW: build_trace_metadata
│   └── outbound_audit.py                   # NEW: record_outbound_sent + record_outbound_failed
│
├── models/
│   ├── outbound_message.py                 # NEW: OutboundMessage ORM
│   └── __init__.py                         # MODIFIED: re-export
│
├── treeflow/
│   ├── runtime.py                          # MODIFIED: graph.ainvoke gets metadata
│   ├── classifier.py                       # MODIFIED: structured.ainvoke gets metadata
│
├── llm/
│   └── extractor.py                        # MODIFIED: runnable.ainvoke gets metadata
│
├── guardrails/
│   └── critic.py                           # MODIFIED: runnable.ainvoke gets metadata
│
├── worker/
│   └── jobs/
│       ├── inbound.py                      # MODIFIED: 6 audit write sites (3 success + 3 failure paths)
│       └── follow_up_scanner.py            # MODIFIED: 4 audit write sites (1 success + 3 failure)
│
├── cli/
│   ├── outbound.py                         # NEW: ai-sdr outbound list
│   └── app.py                              # MODIFIED: register outbound_app
│
├── settings.py                             # MODIFIED: 3 new fields (LangSmith env vars)
└── main.py                                 # MODIFIED: startup validates LangSmith config

migrations/versions/
└── 0011_outbound_messages.py               # NEW

docker-compose.yml                          # MODIFIED: API + worker get 3 env vars
.env.example (if exists)                    # MODIFIED: 3 LangSmith vars commented
pyproject.toml                              # UNCHANGED — langsmith already transitive
CLAUDE.md                                   # MODIFIED: new "Observability (Plano 10)" section
```

---

## 10. Testing strategy

### Unit (in-process, mocked)
- `tests/unit/test_observability_tracing_metadata.py` — `build_trace_metadata` produz dict correto pra combinações de inputs; só inclui chaves passadas; `trace_origin` sempre presente.
- `tests/unit/test_outbound_audit_helpers.py` — helpers constroem `OutboundMessage` com campos certos; respeita XOR (text → body_text, no template_ref).
- `tests/unit/test_outbound_cli.py` — typer commands: list formatting, filtros, --limit, exit codes.

### Integration (DB + RLS, VPS-only)
- `tests/integration/test_outbound_messages_model.py` — RLS isolation, FK cascades (delete lead → cascade), check constraints (status, message_type, body XOR template), partial indexes utilizados.
- `tests/integration/test_outbound_audit_writes_from_inbound.py` — fluxo P5: inbound → worker → send_text success → 1 row outbound_messages com triggered_by='inbound', inbound_message_id correto, status='sent'.
- `tests/integration/test_outbound_audit_writes_from_send_failure.py` — `FakeMessagingAdapter.fail_next_send(RecipientUnreachable)` → worker captura → 1 row status='failed', error_detail contém "RecipientUnreachable".
- `tests/integration/test_outbound_audit_writes_from_window_expired_recovery.py` — fluxo P9: WindowExpired + reengagement template → send_template chamado → 1 row triggered_by='window_expired_recovery', message_type='template'.
- `tests/integration/test_outbound_audit_writes_from_follow_up_scanner.py` — fluxo P9: scanner pega job → send_template → 1 row triggered_by='follow_up_scanner', follow_up_job_id correto, template_params renderizados.
- `tests/integration/test_outbound_cli_integration.py` — `ai-sdr outbound list` hits real DB: lista sent/failed, --lead filtra, ORDER BY DESC, --limit respeitado.

### Live (opt-in, gated)
- `tests/integration/test_langsmith_live.py` (`pytest.mark.live_llm`) — quando `LANGSMITH_API_KEY` setada, 1 ainvoke real numa chain trivial → espera 1s → GET na API do LangSmith confirma trace recebido com `metadata.tenant_slug` e `metadata.trace_origin`.

### Não testados (decisão deliberada)
- LangSmith server outage (lib engole erro nativamente)
- Sampling (100% em v1)
- Cost calculation (LangSmith server-side)
- Trace ordering / parent-child (langchain auto-gerenciado)

---

## 11. Hooks pra planos futuros

| Plano | Hook |
|---|---|
| **P11b — Conversation viewer** | UI que junta `inbound_messages` + `outbound_messages` por lead numa timeline. Schema P10 já carrega o que precisa (sent_at, body_text, template_ref, status, error_detail, triggered_by). |
| **P11d — Manual takeover** | Operador escreve manualmente; insere outbound_messages com `triggered_by='manual_takeover'` (novo enum value). Migration de alter check constraint. |
| **Observability scale-up** | Adiciona Prometheus + Grafana quando volume justificar. LangSmith continua pra LLM traces. |
| **Alerting** | Plano dedicado que consome `outbound_messages.status='failed'` rate → Slack/email. |
| **Cost dashboard** | Já cai automático no LangSmith UI. Plano custom só se quiser visão multi-tenant agregada. |
| **2-phase outbox commit** | Plano dedicado pra fechar a race window do §7. |
| **`@traceable` em ops não-LLM** | Plano dedicado adiciona spans customizados em worker/scanner/webhooks via `langsmith.traceable` decorator. |

---

## 12. Open questions

Nenhuma.

Decisões marginais deferred:
- Migration order vs P9 — P10 = 0011 só funciona se P9 (0010) tiver merge-ado antes. Coordinator handles at merge time.
- `audit_lost` race mitigation — log warning suficiente em v1; outbox-pattern fica pra plano dedicado.
- LangSmith dashboard customization — usamos UI default do LangSmith Cloud. Customizações ficam fora.

---

**Fim do spec.**
