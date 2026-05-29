# Spec: Follow-up scheduler + WhatsApp HSM templates (Plano 9)

**Data:** 2026-05-27
**Status:** Aceito (brainstorm fechado, pronto pra plano)
**Autor:** Nicolas Amaral (decisão com Claude)
**Referências:**
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) — spec master, §4.6 (Follow-up Scheduler), §5.1 (Follow-up por TreeFlow)
- [`2026-05-24-messaging-adapter-design.md`](./2026-05-24-messaging-adapter-design.md) — Plano 5, define `WindowExpiredError` como hook explícito pra P9
- [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) — ADR adapter pattern (P9 estende contrato adicionando `send_template`)

---

## 1. Contexto

Hoje (após P5) o PeSDR fala com o lead quando ele manda mensagem inbound. Não tem mecanismo pra **reengajar lead que some**. Isso quebra a conversion rate por dois cenários reais:

1. **Lead some por 24h+**: WhatsApp Cloud API fecha a janela de mensagem livre. Worker que tente `send_text` recebe `WindowExpiredError` (P5 já levanta esse erro tipado). Hoje: marca msg como `error`, log warning, RETORNA. Lead fica sem resposta.
2. **Agente segue silêncio**: lead respondeu mas depois não voltou. Sem follow-up proativo, o thread morre.

Plano 9 entrega **a cadência de follow-up declarativa por TreeFlow** (já desenhada na spec master §5.1) **+** o **mecanismo de envio de templates HSM via WhatsApp Cloud** que torna possível mensagem fora da janela de 24h.

---

## 2. Decisão (síntese)

PeSDR ganha:

- **Tabela `follow_up_jobs`** (tenant-scoped, RLS) — agendador persistente.
- **arq.cron `follow_up_scanner`** rodando a cada 60s — pega due jobs e dispatcha.
- **Per-lead serialização** via `pg_advisory_lock` (mesmo padrão de `process_lead_inbox` do P5).
- **`MessagingAdapter.send_template(to, ref, lang, params)` adicionado ao contrato** — extensão aditiva, sem breaking change.
- **TreeFlow.follow_up config** — declarativa: `enabled`, `max_attempts`, `sequence: [{after, template_ref, language, params}]`. Cada attempt aponta pra um HSM template já aprovado na Meta.
- **TalkFlow ganha 3 colunas** — `last_agent_message_at`, `last_lead_message_at`, `follow_up_attempt_number` (rastreio temporal pra contador + race-belt).
- **`tenant.yaml > messaging.reengagement_template`** opcional — usado pra recovery reativo quando `send_text` bate `WindowExpiredError`.
- **CLI ops** — `ai-sdr follow-ups list/cancel/dry-run`.

---

## 3. Não-objetivos

- **Editor de templates** (UI ou CLI) — Meta Business Manager é source-of-truth do conteúdo; nosso YAML só referencia.
- **Cross-tenant follow-up dashboard** — fica pra P11b/c (HITL UI expansion).
- **Métricas de conversion rate por template** — P10 (Observability).
- **Follow-up por Node** (override do TreeFlow) — V2, master spec já marca como "não no MVP". Config a nível de TreeFlow basta.
- **Templates dinâmicos** (texto livre + fill) — Meta não suporta. Apenas `parameters` posicionais em templates pré-aprovados.
- **Notificação ao operador quando follow-up esgota** — log estruturado já fica em pé (`follow_up.exhausted_marked_cold`); UI de alert é P10.
- **Outbound messages table** (audit completo de mensagens enviadas) — P10 introduz. P9 grava somente `follow_up_jobs.sent_external_id` pra rastreio do template específico.
- **Multi-channel templates** (Instagram, etc) — escopado a WhatsApp Cloud. Outros providers implementam quando chegarem.
- **Per-tenant override do scanner interval** — todos rodam 1×/min.

---

## 4. Arquitetura

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Inbound path (P5, modificado pelo P9)                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│  webhook → ingest → process_lead_inbox:                                       │
│    1. cancela follow_up_jobs(status='pending') do lead                        │
│    2. reset talkflow.follow_up_attempt_number=0                               │
│    3. cold→active se talkflow estava cold                                     │
│    4. talkflow.last_lead_message_at = msg.received_at                         │
│    5. runtime.step → response_text                                            │
│    6. adapter.send_text                                                       │
│       ├── on success: talkflow.last_agent_message_at = now()                  │
│       │              + schedule follow_up_jobs(attempt=1, scheduled_at=...)   │
│       └── on WindowExpiredError: fallback pra send_template(reengagement)     │
│           └── if no reengagement_template: marca error                        │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│  Follow-up path (P9, NEW)                                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│  arq.cron follow_up_scanner (a cada 60s):                                     │
│    SELECT id, tenant_id, lead_id FROM follow_up_jobs                          │
│      WHERE status='pending' AND scheduled_at <= now()                         │
│      LIMIT 200                                                                │
│      (com SET LOCAL row_security=off — cross-tenant query)                    │
│                                                                                │
│  Per job:                                                                      │
│    1. set_tenant_context(job.tenant_id)                                       │
│    2. pg_try_advisory_lock(hash(tenant_id, lead_id))                          │
│       └── miss → return (próximo scan retenta)                                │
│    3. re-load job, talkflow, lead                                             │
│    4. race-belt: talkflow.last_lead_message_at > job.scheduled_at?            │
│       └── sim: marca cancelled, return                                        │
│    5. talkflow.status in (cold, completed)? → cancelled, return               │
│    6. load TreeFlow.follow_up + render params (Jinja2)                        │
│    7. adapter.send_template(to, ref, lang, params)                            │
│       ├── success: marca completed, talkflow.last_agent_message_at=now()      │
│       │            + increment follow_up_attempt_number                        │
│       │            + if attempt == max: talkflow.status='cold'                │
│       │              senão: schedule next attempt                              │
│       ├── RecipientUnreachable: marca lead.status='unreachable'               │
│       │                        + cancela todos pending do lead                │
│       └── Auth/Policy/Messaging: marca job error, alerta                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Princípios:**

1. **Schedule-one-at-a-time**: cada job firado agenda só o próximo. Permite que mudanças in-flight no `treeflow.follow_up.sequence` apliquem (Q2).
2. **Storage em DB com scanner cron**: tabela `follow_up_jobs` é fonte da verdade. arq.cron é o trigger temporal. Visibility + cancelabilidade triviais.
3. **Per-lead lock interleaves com P5**: scanner e `process_lead_inbox` usam o MESMO `pg_try_advisory_lock(hash(tenant, lead))`. Garante serialização entre inbound processing e follow-up firing.
4. **Race-belt no firing**: `last_lead_message_at > scheduled_at` é re-checado no momento do fire — protege contra lead-respondeu-entre-scheduling-e-scan.
5. **Templates referenciados, não inlinados**: `template_ref` aponta pra nome registrado na Meta. Source-of-truth do CONTEÚDO é a Meta Business Manager. Trocar texto requer atualizar lá, não no nosso YAML.
6. **Recovery WindowExpired é reativo**: detecta o erro mid-send, troca pra template; não tenta pre-empt. Resposta livre do agente é perdida nessa rara borda (race entre LLM call e janela fechar) — trade-off aceitável.

---

## 5. Modelo de dados

### Migration `0010_follow_up_and_talkflow_columns.py`

```sql
CREATE TABLE follow_up_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    talkflow_id UUID NOT NULL REFERENCES talkflows(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    fired_at TIMESTAMPTZ,
    sent_external_id TEXT,
    error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_follow_up_jobs_status CHECK (
        status IN ('pending', 'completed', 'cancelled', 'error')
    ),
    CONSTRAINT ck_follow_up_jobs_attempt_positive CHECK (attempt_number >= 1)
);
CREATE INDEX ix_follow_up_jobs_due
    ON follow_up_jobs (scheduled_at) WHERE status = 'pending';
CREATE INDEX ix_follow_up_jobs_lead_pending
    ON follow_up_jobs (lead_id) WHERE status = 'pending';

ALTER TABLE follow_up_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE follow_up_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY follow_up_jobs_tenant_isolation ON follow_up_jobs
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- TalkFlow extensions
ALTER TABLE talkflows ADD COLUMN last_agent_message_at TIMESTAMPTZ;
ALTER TABLE talkflows ADD COLUMN last_lead_message_at TIMESTAMPTZ;
ALTER TABLE talkflows ADD COLUMN follow_up_attempt_number INTEGER NOT NULL DEFAULT 0;
```

**Notas:**
- `talkflow_id` é redundante com `lead_id` (cada lead tem 1 talkflow ativo via UNIQUE constraint do P5 migration 0008). Mantida pra simplificar lookups + permitir múltiplos talkflows futuros sem schema change.
- Index parcial `ix_follow_up_jobs_due` é o quente do scanner (1×/min hit).
- Index parcial `ix_follow_up_jobs_lead_pending` cobre o `UPDATE ... WHERE lead_id=X AND status='pending'` do cancelamento bulk.

### TreeFlow YAML schema additions

```python
# src/ai_sdr/schemas/treeflow_yaml.py

class FollowUpStep(BaseModel):
    after: str                                  # ISO-8601 duration: "PT24H", "P7D"
    template_ref: str                           # Meta Business Manager template name
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)  # Jinja2 templates

    @field_validator("after")
    @classmethod
    def _check_iso_duration(cls, v: str) -> str:
        from ai_sdr.follow_up.duration import parse_duration
        try:
            parse_duration(v)
        except Exception as e:
            raise ValueError(f"invalid ISO-8601 duration {v!r}: {e}") from e
        return v


class FollowUpConfig(BaseModel):
    enabled: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)
    sequence: list[FollowUpStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_sequence_length(self) -> "FollowUpConfig":
        if self.enabled and len(self.sequence) < self.max_attempts:
            raise ValueError(
                f"follow_up.sequence has {len(self.sequence)} entries but "
                f"max_attempts={self.max_attempts} — need at least max_attempts entries"
            )
        return self


# Existing TreeFlow gains:
class TreeFlow(BaseModel):
    # ... existing fields ...
    follow_up: FollowUpConfig | None = None
```

### Tenant YAML schema additions

```python
# src/ai_sdr/schemas/tenant_yaml.py

class ReengagementTemplate(BaseModel):
    template_ref: str
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)


class MessagingConfig(BaseModel):
    # ... existing fields from P5 ...
    reengagement_template: ReengagementTemplate | None = None
```

### `MessagingAdapter.send_template` (contract addition)

```python
# src/ai_sdr/messaging/base.py

class MessagingAdapter(ABC):
    # ... handle_inbound, send_text, verification_challenge (existing) ...

    @abstractmethod
    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        """Send a pre-approved HSM template. Provider validates template_ref
        + language + params shape against its pre-registered templates.

        Returns SendResult (same shape as send_text).
        Raises typed terminal errors per existing taxonomy:
          - AuthError, RecipientUnreachable, PolicyError
          - WindowExpiredError should NEVER fire (templates bypass 24h window)
        Internally retries Transient/RateLimit with same backoff as send_text.
        """
```

Aditivo — não quebra impls existentes. `FakeMessagingAdapter` e `WhatsAppCloudAPIAdapter` ganham impl no Plano 9.

---

## 6. Algoritmos críticos

### 6.1 Inbound processing extensions (`process_lead_inbox`)

Após carregar `talkflow` e ANTES de chamar `runtime.step`:

```python
# Cancel pending follow-ups (Q3 reset, Q6 cancellation)
await db.execute(
    update(FollowUpJob)
    .where(FollowUpJob.lead_id == lead.id, FollowUpJob.status == "pending")
    .values(status="cancelled", error_detail="lead responded")
)
talkflow.follow_up_attempt_number = 0
talkflow.last_lead_message_at = msg.received_at

# Cold → active (lead voltou)
if talkflow.status == "cold":
    talkflow.status = "active"
    log.info("follow_up.cold_reactivated", talkflow_id=str(talkflow.id))
```

Após `adapter.send_text` success, ANTES de marcar msg.status='processed':

```python
talkflow.last_agent_message_at = datetime.now(UTC)

# Schedule first follow-up if treeflow enables
tf_config = await load_treeflow_follow_up(db, talkflow)
if tf_config and tf_config.enabled and tf_config.sequence:
    first_step = tf_config.sequence[0]
    db.add(FollowUpJob(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        attempt_number=1,
        scheduled_at=datetime.now(UTC) + parse_duration(first_step.after),
        status="pending",
    ))
```

No `except WindowExpiredError`:

```python
reeng = tenant_cfg.messaging.reengagement_template
if reeng is not None:
    try:
        params = render_params(reeng.params, lead, talkflow, tenant)
        await adapter.send_template(
            to=lead.whatsapp_e164,
            template_ref=reeng.template_ref,
            language=reeng.language,
            params=params,
        )
        msg.status = "processed"
        msg.error_detail = "window_expired; recovered via reengagement template"
        talkflow.last_agent_message_at = datetime.now(UTC)
        log.info("messaging.window_expired_recovered", lead_id=str(lead.id))
    except Exception as e2:
        msg.status = "error"
        msg.error_detail = f"window_expired; reengagement failed: {e2}"
        log.warning("messaging.reengagement_failed", lead_id=str(lead.id), err=str(e2))
else:
    msg.status = "error"
    msg.error_detail = f"window_expired: {e}"
    log.warning("messaging.window_expired_no_template", lead_id=str(lead.id))
await db.commit()
return
```

### 6.2 Scanner cron job

```python
# src/ai_sdr/worker/jobs/follow_up_scanner.py

async def follow_up_scanner(ctx: dict) -> None:
    """Runs every 60s via arq cron. Picks due jobs and dispatches them."""
    session_factory = ctx["session_factory"]
    registry = ctx["adapter_registry"]

    async with session_factory() as db:
        # Cross-tenant scan — bypass RLS for this read only.
        await db.execute(text("SET LOCAL row_security = off"))
        due_jobs = (await db.execute(
            select(FollowUpJob.id, FollowUpJob.tenant_id, FollowUpJob.lead_id)
            .where(
                FollowUpJob.status == "pending",
                FollowUpJob.scheduled_at <= func.now(),
            )
            .order_by(FollowUpJob.scheduled_at.asc())
            .limit(200)
        )).all()

    for row in due_jobs:
        try:
            await _fire_follow_up(session_factory, registry, row.id, row.tenant_id, row.lead_id)
        except Exception:
            log.exception("follow_up.scanner.job_failed", job_id=str(row.id))
```

### 6.3 Per-job firing (`_fire_follow_up`)

Detalhado na Seção 3 da apresentação de design (transcrito no plano de implementação). Resumo crítico:
1. set_tenant_context
2. pg_try_advisory_lock per (tenant, lead)
3. re-load job + talkflow + lead
4. race-belt: `talkflow.last_lead_message_at > job.scheduled_at` → cancelled
5. cold/completed → cancelled
6. load follow_up config from TreeflowVersion's content_yaml
7. render params (Jinja2 sandboxed)
8. adapter.send_template
9. handle 4 error paths (RecipientUnreachable cascade-cancels; Auth/Policy/MessagingError marks job error)
10. on success: increment attempt + schedule next OR mark cold

### 6.4 Jinja sandbox

Render usa `jinja2.sandbox.SandboxedEnvironment`. Variáveis disponíveis:
- `collected.<field>` — campos extraídos pelo TreeFlow (do TalkFlowState.collected — leitura do LangGraph checkpointer)
- `lead.whatsapp_e164`, `lead.external_label`
- `tenant.slug`, `tenant.display_name`

Filtros permitidos: `default`, `lower`, `upper`, `trim`, `truncate(N)`. Nada de `__class__`, `__getattribute__`, `_*`, etc. — sandbox bloqueia.

### 6.5 Duration parser

`isodate>=0.6` lib (~12KB). `parse_duration("PT24H") → timedelta(hours=24)`. Cobre `PT*S/M/H`, `P*D/W/M/Y`. Errors levantam ValueError no validator do schema.

---

## 7. CLI ops (`ai-sdr follow-ups`)

```bash
ai-sdr follow-ups list --tenant <slug> [--lead <uuid>] [--status pending|completed|cancelled|error|all]
# Tabela rich: id | lead | attempt | scheduled_at | status | template_ref | sent_id
# Default: --status=pending. --lead filtra. Sem flags: todos pending do tenant.

ai-sdr follow-ups cancel --tenant <slug> --lead <uuid>
# UPDATE follow_up_jobs SET status='cancelled', error_detail='manual'
# WHERE lead_id=X AND status='pending'. Output: contagem cancelada.

ai-sdr follow-ups dry-run --tenant <slug> --treeflow <id> --lead <uuid>
# Pra debug de config:
#   1. Carrega TreeFlow.follow_up + lead + talkflow
#   2. Calcula próximo attempt baseado em follow_up_attempt_number
#   3. Renderiza params via Jinja
#   4. Mostra: template_ref + language + params renderizados + scheduled_at calculado
# NÃO chama adapter, NÃO INSERTa nada no DB.
```

Implementação em `src/ai_sdr/cli/follow_ups.py`. Mesma pattern de session-via-engine dos outros CLI (`simulate`, `users`).

---

## 8. Module layout

```
src/ai_sdr/
├── follow_up/                              # NEW package — shared helpers
│   ├── __init__.py
│   ├── duration.py                         # NEW: parse_duration(iso) → timedelta
│   ├── jinja.py                            # NEW: render_params(params, lead, talkflow, tenant)
│   ├── treeflow_loader.py                  # NEW: load_treeflow_follow_up(db, talkflow) → FollowUpConfig|None
│   └── scheduler.py                        # NEW: schedule_next_followup, cancel_pending_followups, mark_cold helpers
│
├── messaging/
│   ├── base.py                             # MODIFIED: send_template abstract method
│   ├── whatsapp_cloud.py                   # MODIFIED: implements send_template (POST /messages type=template)
│   └── fake.py                             # MODIFIED: implements send_template (records to sent_templates list)
│
├── models/
│   ├── follow_up_job.py                    # NEW: FollowUpJob ORM
│   ├── talkflow.py                         # MODIFIED: 3 new columns (last_agent/lead_message_at, attempt_number)
│   └── __init__.py                         # MODIFIED: re-export FollowUpJob
│
├── schemas/
│   ├── treeflow_yaml.py                    # MODIFIED: FollowUpStep + FollowUpConfig + follow_up field on TreeFlow
│   └── tenant_yaml.py                      # MODIFIED: ReengagementTemplate + reengagement_template on MessagingConfig
│
├── worker/
│   ├── main.py                             # MODIFIED: cron_jobs=[cron(follow_up_scanner, ...)]
│   └── jobs/
│       ├── inbound.py                      # MODIFIED: 3 changes per §6.1
│       └── follow_up_scanner.py            # NEW: scanner + _fire_follow_up
│
├── cli/
│   ├── follow_ups.py                       # NEW: ai-sdr follow-ups {list,cancel,dry-run}
│   └── app.py                              # MODIFIED: register follow_ups_app

migrations/versions/
└── 0010_follow_up_and_talkflow_columns.py  # NEW

tenants/example/
├── tenant.yaml                             # MODIFIED: messaging.reengagement_template (commented, opt-in)
└── treeflows/example.yaml                  # MODIFIED: follow_up section (max_attempts=2, sequence x2)

pyproject.toml                              # MODIFIED: add isodate>=0.6
CLAUDE.md                                   # MODIFIED: new "Follow-up + HSM templates (Plano 9)" section

tests/
├── unit/
│   ├── test_follow_up_duration.py          # NEW
│   ├── test_follow_up_jinja.py             # NEW (sandbox + safe filters)
│   ├── test_follow_up_config_schema.py     # NEW
│   ├── test_reengagement_template_schema.py # NEW
│   ├── test_messaging_base_send_template.py # NEW (ABC enforcement)
│   ├── test_fake_send_template.py          # NEW
│   ├── test_whatsapp_send_template_payload.py # NEW (mocked httpx)
│   └── test_follow_ups_cli.py              # NEW
└── integration/
    ├── test_follow_up_jobs_model.py        # NEW (RLS + FK + check constraints)
    ├── test_follow_up_scanner_basic.py     # NEW
    ├── test_follow_up_scanner_race_belt.py # NEW
    ├── test_follow_up_scanner_serializes.py # NEW (per-lead lock)
    ├── test_follow_up_full_lifecycle.py    # NEW (1-3 attempts + cold + reactivation)
    ├── test_follow_up_cancellation_on_inbound.py # NEW
    ├── test_window_expired_recovery.py     # NEW
    ├── test_window_expired_no_template_fallback.py # NEW
    ├── test_follow_up_recipient_unreachable.py # NEW (cascade cancel)
    └── test_adapter_compliance.py          # MODIFIED (adicionar testes de send_template a fake + whatsapp_cloud_mocked params)
```

---

## 9. Testing strategy

Detalhada na Seção 5 da apresentação. Resumo:

- **Unit**: 8 arquivos, ~30 tests. Cobertura: duration parsing, Jinja sandbox safety, schema validators, ABC enforcement, fake adapter scripting, WhatsApp payload shape, CLI rendering.
- **Integration (VPS-only)**: 10 arquivos, ~25 tests. Cobertura: RLS + FK do `follow_up_jobs`, scanner mechanics (basic + race-belt + lock serialization), lifecycle completo (1→3 attempts → cold → lead volta → reactivation), cancellation on inbound, WindowExpired recovery (both with and without reengagement template configured), RecipientUnreachable cascade cancellation, adapter-compliance suite extension.
- **Live (opt-in)**: 1 file gated by `LIVE_WHATSAPP=1` — manda HSM real pro número de teste do operador.

---

## 10. Hooks pra planos futuros

| Plano | Hook |
|---|---|
| **P10 — Observability** | `outbound_messages` table dedicada (audit completo). Métricas Prometheus em `follow_up.send.*`, `follow_up.scanner.*`. Alert quando taxa de erro de template > threshold. |
| **P11b — HITL UI conversation viewer** | Junta `inbound_messages` + outbound audit (P10) + `follow_up_jobs` numa timeline visual por lead. |
| **P11d — Manual takeover** | Operador cancela pending follow-ups via UI (consome a REST/CLI já existente: `ai-sdr follow-ups cancel`). |
| **P12 — Production polish** | Rate limit nos sends pra evitar burst no Meta API. Retry com exponential backoff já existe em `tenacity` no WhatsAppCloudAPIAdapter — pode ser reaproveitado. |
| **Plano Vialum-integration** | `VialumChatAdapter.send_template` cumprindo o contrato. Adapter-compliance suite valida automaticamente. |
| **V2 — Follow-up por Node** | Schema permite via `node.follow_up_override` (TreeflowSpec gains field). Worker checa node-level antes de cair pra treeflow-level. |

---

## 11. Open questions

Nenhuma.

Decisões marginais deferred (não bloqueiam v1):
- **Tunelar scanner interval por tenant** — todos rodam 1×/min (Plano 12 se necessário).
- **Outbound messages audit** — fica pra P10; P9 grava apenas `follow_up_jobs.sent_external_id`.
- **Rate limiting agressivo no Meta API** — P12 (production polish).

---

**Fim do spec.**
