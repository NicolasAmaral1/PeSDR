# Spec: Messaging Adapter + WhatsApp Cloud default (Plano 5)

**Data:** 2026-05-24
**Status:** Aceito (brainstorm fechado, pronto pra plano)
**Autor:** Nicolas Amaral (decisão com Claude)
**Referências:**
- [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) — spec master
- [`2026-05-24-adapter-pattern-decision.md`](./2026-05-24-adapter-pattern-decision.md) — ADR adapter pattern (esta é a 1ª aplicação)
- [`2026-05-23-kb-and-guardrails-design.md`](./2026-05-23-kb-and-guardrails-design.md) — Plano 3 (T2b multi-provider LLM, template do factory dispatch)

---

## 1. Contexto

Plano 5 é a primeira aplicação concreta da ADR de adapter pattern (3 bordas: Messaging, Identity, HITL). Antes da ADR, o plano era "WhatsApp Cloud Adapter direto". Agora é "abstração `MessagingAdapter` + `WhatsAppCloudAPIAdapter` como default standalone".

Até aqui (Planos 1–3) PeSDR roda TreeFlow + KB + Guardrails, mas só consegue conversar via CLI (`ai-sdr simulate`). Plano 5 é o que tira o sistema do terminal — webhook + worker + delivery real pra WhatsApp.

Plano 6 (Identity) formaliza `IdentityResolver`; Plano 5 introduz o helper ad-hoc que vira a `InternalLead` impl no Plano 6 sem refactor.

Plano 9 (Follow-up) usa o `WindowExpiredError` deste plano como hook pra disparar templates HSM. Plano 8 (Media) adiciona métodos `send_media`/`receive_media` ao contrato (extensão aditiva, sem breaking change).

---

## 2. Decisão (síntese)

PeSDR ganha módulo `src/ai_sdr/messaging/` com:

- **Contrato abstrato** `MessagingAdapter` (handle_inbound + send_text + verification_challenge).
- **Impl default standalone** `WhatsAppCloudAPIAdapter` (HMAC verify, parsing, send, retry com classificação tipada de erros).
- **Factory dispatch** por `tenant.yaml > messaging.provider` (mesmo padrão de `init_chat_model` do Plano 3 T2b).
- **Webhook routes** FastAPI: `/webhooks/{tenant_slug}/{provider}` (GET pra challenge, POST pra ingestão).
- **Worker arq separado** (`ai-sdr worker`) processando fila Redis com per-lead Postgres advisory lock.
- **Modelo `leads`** (mínimo: id, tenant_id, whatsapp_e164, status, unreachable_reason).
- **Modelo `inbound_messages`** (dedupe + audit + replay queue).
- **Bootstrap HITL-friendly**: lead novo nasce `pending_assignment`; mensagens ficam queued até operador atribuir treeflow via CLI `ai-sdr assign-lead` ou `POST /tenants/{slug}/leads/{id}/assign`. Atribuição dispara replay de **todas** as inbounds acumuladas.
- **Sem auto-ack** enquanto lead está pending (configurável no futuro, default silêncio).

---

## 3. Não-objetivos

- **Sem media** (Plano 8 — audio/image/STT/Vision).
- **Sem templates HSM** (Plano 9 — `WindowExpiredError` é o hook).
- **Sem fila dedicada pra outbound** — worker chama `send_text` síncrono. Plano 9 traz scheduler de follow-up que vai precisar fila própria.
- **Sem `VialumChatAdapter`** — Plano "Vialum-integration" separado.
- **Sem UI HITL** — Plano 11. CLI + REST cobrem operação manual de atribuição.
- **Sem alertas/observability formais** — Plano 10. Logging estruturado (structlog) já fica em pé.
- **Sem rate limiting da API** — Plano 12 (production polish).
- **Sem multi-channel num adapter só** — cada adapter é 1 canal.

---

## 4. Arquitetura

```
┌──────────────────┐   POST /webhooks/{tenant}/{provider}
│  WhatsApp Cloud  │ ─────────────────────────────────────► ┌─────────────────┐
└──────────────────┘                                         │  FastAPI route  │
                                                             │  (webhook)      │
                                                             └────────┬────────┘
                                       handle_inbound() + dedupe insert
                                                             ▼
                                                   ┌──────────────────────┐
                                                   │ inbound_messages     │
                                                   │ (status='queued')    │
                                                   └─────────┬────────────┘
                                                             │
                                       arq.enqueue_job("process_lead_inbox", tenant, lead)
                                                             ▼
                                                   ┌──────────────────────┐
                                                   │  Redis queue (arq)   │
                                                   └─────────┬────────────┘
                                                             │
                       ┌─────────────────────────────────────┘
                       ▼
              ┌──────────────────┐   pg_advisory_lock(hash(tenant,lead))
              │  ai-sdr worker   │ ──► load queued msgs ASC
              │  (arq process)   │ ──► runtime.step(user_input=msg.text)
              └────────┬─────────┘ ──► adapter.send_text(to=msg.from_address, text=response)
                       │            ──► mark msg processed; loop until queue empty
                       │
                       ▼
              ┌──────────────────┐
              │  WhatsApp Cloud  │ ◄── POST /messages
              └──────────────────┘
```

Princípios:
1. Webhook handler **nunca chama LLM**. Apenas verify + parse + dedupe insert + enqueue. Retorna 200 em <100ms.
2. Worker é processo separado. Diferentes leads = paralelos; mesmo lead = serializado via advisory lock (ordem preservada).
3. Adapter é **puro**: zero conhecimento de `leads`/`tenants` tables. Fala endereço nativo (`to: str`) opaco no contrato.
4. Dedupe via UNIQUE `(tenant_id, provider, external_id)` na `inbound_messages` — idempotência de webhook é garantida pelo banco.
5. Erros do provider viram exceptions tipadas; cada uma mapeia pra ação concreta (mark lead unreachable / alert ops / Plano 9 hook).

---

## 5. Interface `MessagingAdapter`

Arquivo: `src/ai_sdr/messaging/base.py`.

```python
@dataclass(frozen=True)
class InboundMessage:
    external_id: str           # provider-native id (dedupe key)
    from_address: str          # provider-native address (E.164 for WhatsApp)
    text: str                  # text body
    received_at_iso: str       # ISO 8601, from provider
    raw: Mapping[str, object]  # full original payload for audit/replay

@dataclass(frozen=True)
class SendResult:
    external_id: str
    sent_at_iso: str

class MessagingAdapter(ABC):
    """Boundary between PeSDR runtime and a messaging provider.
    Pure: zero knowledge of leads/tenants tables.
    Tenant config + secrets injected at construction by factory."""

    @abstractmethod
    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        """Verify signature, parse, normalize. Returns [] for status updates
        or no-op payloads. Raises SignatureError if HMAC fails."""

    @abstractmethod
    async def send_text(self, to: str, text: str) -> SendResult:
        """Deliver text. Adapter retries Transient/RateLimit internally with
        bounded backoff. Raises typed terminal errors otherwise."""

    @abstractmethod
    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        """For providers with a GET-based webhook challenge (WhatsApp's
        hub.challenge). Returns the token to echo, or None if N/A."""
```

**Exceptions** (`messaging/errors.py`):

```python
class MessagingError(Exception): pass
class SignatureError(MessagingError): pass

class TerminalError(MessagingError): pass
class AuthError(TerminalError): pass               # → alert ops
class RecipientUnreachable(TerminalError): pass    # → mark lead.unreachable
class PolicyError(TerminalError): pass             # → log + alert
class WindowExpiredError(TerminalError): pass      # → Plano 9 hook

# Internal-only (never escape the adapter):
class TransientError(MessagingError): pass
class RateLimitError(TransientError):
    retry_after_s: int
```

**Factory** (`messaging/factory.py`): dict-based dispatch by `cfg.provider` string. Same pattern as `init_chat_model`.

Convenção: `to: str` é **opaco** no contrato — formato é provider-specific (WhatsApp = E.164, Vialum Chat = `vialum_contact_id` etc.). Adapter sabe; runtime não precisa saber.

---

## 6. Modelo de dados

**Migration 0006 — `leads`:**

```sql
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    whatsapp_e164 TEXT,
    external_label TEXT,          -- human-readable id (used by simulate's --lead flag)
    status TEXT NOT NULL DEFAULT 'pending_assignment',
        -- 'pending_assignment' | 'active' | 'unreachable'
    unreachable_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_leads_tenant_wa ON leads (tenant_id, whatsapp_e164)
    WHERE whatsapp_e164 IS NOT NULL;
CREATE UNIQUE INDEX uq_leads_tenant_label ON leads (tenant_id, external_label)
    WHERE external_label IS NOT NULL;
CREATE INDEX ix_leads_tenant_status ON leads (tenant_id, status);

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
CREATE POLICY leads_tenant_isolation ON leads
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

**Migration 0007 — `inbound_messages`:**

```sql
CREATE TABLE inbound_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    lead_id UUID REFERENCES leads(id) ON DELETE SET NULL,
    from_address TEXT NOT NULL,
    text TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'queued',
        -- 'queued' | 'processed' | 'skipped_dedupe' | 'error'
    processed_at TIMESTAMPTZ,
    error_detail TEXT,
    raw JSONB NOT NULL
);
CREATE UNIQUE INDEX uq_inbound_provider_extid
    ON inbound_messages (tenant_id, provider, external_id);
CREATE INDEX ix_inbound_lead_status
    ON inbound_messages (lead_id, status) WHERE status IN ('queued','error');

ALTER TABLE inbound_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY inbound_messages_tenant_isolation ON inbound_messages
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

**Migration 0008 — `talkflows.lead_id` vira FK pra `leads.id`:**

Hoje é `String(128)` opaco (e.g., `"test-1"` do simulate). Vira `UUID NOT NULL REFERENCES leads(id)`. Backfill: pra cada distinct `(tenant_id, lead_id)` em `talkflows`, cria 1 lead (`status='active'`, `whatsapp_e164=NULL`, `external_label=<string antiga>`); atualiza FK. UNIQUE constraint `(tenant_id, lead_id)` em talkflows é recriada na coluna UUID. Em dev (DBs vazias) é no-op.

---

## 7. Webhook ingestion (FastAPI)

Arquivo: `src/ai_sdr/api/routes/webhooks.py`.

- `GET /webhooks/{tenant_slug}/{provider}` → resolve adapter → `adapter.verification_challenge(query_params)` → echo ou 404.
- `POST /webhooks/{tenant_slug}/{provider}` → resolve adapter → `adapter.handle_inbound(raw_body, headers)` → pra cada `InboundMessage`, `ingest_inbound_message()` (find-or-create lead + INSERT ON CONFLICT DO NOTHING) → commit → enqueue **1 job por lead afetado**.

Helper `ingest_inbound_message` (em `src/ai_sdr/messaging/ingest.py`):

```python
async def ingest_inbound_message(
    db: AsyncSession, tenant: Tenant, provider: str, msg: InboundMessage
) -> IngestResult:
    lead = await find_or_create_lead_by_address(
        db, tenant.id, provider, msg.from_address
    )
    # ORM model: `InboundMessageRow` (renomeado pra evitar colisão com o
    # dataclass `InboundMessage` do messaging/base.py).
    inserted = await db.execute(
        insert(InboundMessageRow).values(
            tenant_id=tenant.id, provider=provider, external_id=msg.external_id,
            lead_id=lead.id, from_address=msg.from_address, text=msg.text,
            received_at=msg.received_at_iso, raw=dict(msg.raw), status="queued",
        ).on_conflict_do_nothing()
    )
    if inserted.rowcount == 0:
        return IngestResult(status="skipped_dedupe", lead_id=lead.id)
    return IngestResult(status="queued", lead_id=lead.id)
```

Comportamento:
- 1 job por lead afetado (não 1 por mensagem) — preserva ordem no worker via re-scan loop.
- WhatsApp retentar mesmo `external_id` é no-op silencioso (UNIQUE constraint).
- SignatureError → HTTP 401. Outras exceptions deixam o handler explodir (alerta via Plano 10).

Adapter registry: cache `(tenant_id, provider) → adapter instance` em singleton thread-safe. Invalida quando tenant.yaml muda (filewatch dev / restart prod).

**Validação URL ↔ config:** se o `{provider}` da URL não bate com `tenant.yaml > messaging.provider`, retorna 404. Evita confusão silenciosa (tenant configurado pra `vialum_chat` recebendo `/webhooks/joana/whatsapp_cloud`).

---

## 8. Worker (arq)

CLI: `uv run ai-sdr worker` (typer command bootstrapping arq `WorkerSettings`).

Job único do Plano 5: `process_lead_inbox(tenant_id: str, lead_id: str)`.

Lógica:

```python
async def process_lead_inbox(ctx, tenant_id: str, lead_id: str) -> None:
    async with session_factory() as db:
        await set_tenant_context(db, UUID(tenant_id))
        lock_key = stable_hash(tenant_id, lead_id)
        got = (await db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
        )).scalar()
        if not got:
            return  # another worker has this lead; its loop will pick up new msgs

        try:
            tenant = await load_tenant(db, UUID(tenant_id))
            lead = await load_lead(db, UUID(lead_id))

            if lead.status == "pending_assignment":
                return  # waiting on operator

            if lead.status == "unreachable":
                await mark_queued_as_skipped(db, lead.id, reason="lead_unreachable")
                await db.commit()
                return

            # lead.status == 'active'
            talkflow = await find_active_talkflow_for_lead(db, lead.id)
            adapter = adapter_registry.get(tenant.id, tenant_cfg.messaging.provider)
            while True:
                msg = await fetch_next_queued_inbound(db, lead.id)
                if msg is None:
                    break
                await _process_one(db, tenant, lead, talkflow, msg, adapter)
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
            )
```

`_process_one` chama `runtime.step()` → `adapter.send_text()`. Erros tipados:

| Exception | Ação |
|---|---|
| `RecipientUnreachable` | `lead.status='unreachable'`; msg.status='error'; loop encerra |
| `WindowExpiredError` | msg.status='error' com `error_detail='window_expired'`; logado. Plano 9 hook |
| `AuthError`, `PolicyError` | msg.status='error'; log+alert; loop encerra (sem retry — needs ops) |
| `MessagingError` (catch-all) | msg.status='error'; log; loop encerra |

arq settings:
- `max_tries=3` — safety-net pra crashes do worker (não pra erros tipados, que já foram tratados)
- `job_completion_wait=30`
- `on_startup=ensure_checkpointer_schema` (mesma garantia de schema que a API)

---

## 9. `WhatsAppCloudAPIAdapter`

Arquivo: `src/ai_sdr/messaging/whatsapp_cloud.py`.

Config (`MessagingConfig` em `schemas/tenant_yaml.py`):

```python
class MessagingConfig(BaseModel):
    provider: str
    phone_number_id_ref: str | None = None
    access_token_ref: str | None = None
    webhook_verify_token_ref: str | None = None
    app_secret_ref: str | None = None  # for X-Hub-Signature-256 HMAC
    api_version: str = "v21.0"

    @model_validator(mode="after")
    def check_provider_fields(self):
        if self.provider == "whatsapp_cloud":
            required = ("phone_number_id_ref","access_token_ref",
                        "webhook_verify_token_ref","app_secret_ref")
            for f in required:
                if not getattr(self, f):
                    raise ValueError(f"messaging.whatsapp_cloud requires {f}")
        return self
```

Convenção `secrets/` prefix igual ao LLM (Plano 3 T2b).

**verification_challenge** (GET handshake):
- Verifica `hub.mode == "subscribe"` e `hub.verify_token` casa com config.
- Echo do `hub.challenge`. Token mismatch → `SignatureError`.

**handle_inbound** (POST):
- Verifica HMAC `X-Hub-Signature-256` (sha256 com `app_secret`).
- Parseia payload `{entry: [{changes: [{value: {messages: [...]}}]}]}`.
- Filtra `type=='text'` (Plano 5 só text). `audio`/`image`/`document` → ignorados.
- Status updates (`value.statuses`) → ignorados (retorna `[]`).
- Normaliza `from` (adiciona `+`) e `timestamp` (epoch → ISO).

**send_text** (POST `/{api_version}/{phone_number_id}/messages`):
- Body: `{messaging_product, to, type: "text", text: {body}}`.
- Tenacity AsyncRetrying: 3 tentativas, backoff exponencial (1s, 2s, 4s), respeita `Retry-After` em 429.
- Classificação por error code Meta:

| Status / error_code | → Exception |
|---|---|
| 401, 403, code 190 | `AuthError` |
| 400 code 131026 (recipient not on WA), 131051 | `RecipientUnreachable` |
| 400 code 131047 (24h window) | `WindowExpiredError` |
| 400 code 131048 (spam rate limit), 131049 (policy) | `PolicyError` |
| 429, 503 | `RateLimitError(retry_after_s=int(headers["Retry-After"]))` |
| 5xx (outros), network, timeout | `TransientError` |
| Outros 4xx | `PolicyError` (catch-all conservador) |

Referência: https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes (códigos exatos conferidos na impl).

Logging structlog: `wa.send.start`, `wa.send.success` (com `external_id`, `attempt`), `wa.send.error` (com `error_code`, `error_subcode`).

---

## 10. Lead assignment (CLI + REST)

REST (`src/ai_sdr/api/routes/leads.py`):
- `GET /tenants/{tenant_slug}/leads/pending` → lista `pending_assignment` ordenado por `created_at DESC`.
- `POST /tenants/{tenant_slug}/leads/{lead_id}/assign {treeflow_id}` → cria talkflow via `runtime.create`, muda `lead.status='active'`, enqueue `process_lead_inbox` (drain).
  - 404 se lead não existe; 409 se lead já não é `pending_assignment`.
  - Retorna 202 com `{talkflow_id, queued_messages_to_replay: int}` (contagem de inbounds que o worker vai drenar).

CLI (`src/ai_sdr/cli/leads.py`):
- `ai-sdr list-pending --tenant <slug>` → tabela rich.
- `ai-sdr assign-lead --tenant <slug> --lead <id> --treeflow <id>` → POST endpoint.

CLI consome o REST (não DB direto). Plano 11 UI usa o mesmo endpoint.

---

## 11. Testing

**Adapter-compliance suite** (`tests/messaging/test_adapter_compliance.py`):

Parametrizada por `[whatsapp_cloud_mocked, fake]`. Testes: signature error, normalized inbound shape, empty inbound on status update, send returns external_id, send raises terminal errors corretos, retry de transient/ratelimit. Vialum adapter futuro só adiciona ao param.

**FakeMessagingAdapter** (`src/ai_sdr/messaging/fake.py`):
- In-memory. Permite scripting (`queue_inbound`, `fail_next_send`, `sent_messages`).
- Substitui adapters reais em unit tests do worker.

**WhatsApp adapter unit tests** (`tests/messaging/test_whatsapp_cloud.py`):
- Challenge echo (token válido/inválido).
- HMAC verify com payload real capturado (`tests/fixtures/whatsapp/*.json`).
- Parser: text / non-text ignored / status updates ignored.
- Send: classificação tabular de error codes Meta.

**Worker integration tests** (`tests/integration/test_worker_inbox.py`, requer `make up`):
- Pending lead não dispara step.
- Assign replays N queued in order.
- Dedupe (mesmo external_id 2x).
- Advisory lock serializa concorrência.
- RecipientUnreachable marca lead + restantes viram skipped.

**Webhook route tests** (`tests/api/test_webhooks.py`):
- GET challenge.
- POST 401 em bad signature.
- POST 200 + enfileira em payload válido.
- POST idempotente em duplicate external_id.

**Live test (opcional, gated)** `tests/live/test_whatsapp_live.py`, `LIVE_WHATSAPP=1` + secrets reais + número de teste. Mesma pattern de `test_live_kb_roundtrip.py` (Plano 3 T19).

---

## 12. Impactos periféricos

1. **`ai-sdr simulate`** continua bypassando adapters. Refactor pra usar `FakeMessagingAdapter` é cosmético; deferred.
2. **`TalkFlowRuntime.create`** assinatura: `lead_id: str` → `lead_id: UUID`. CLI `simulate` mantém UX (`--lead test-1`) via find-or-create por `(tenant_id, external_label=<string>)` antes de chamar `create()`. Lead criado por simulate nasce `status='active'` (não pending) pra não trancar no fluxo HITL — simulate é dev tool, não fluxo de produção.
3. **Secrets template** `tenants/example/secrets.enc.yaml` ganha `wa_phone_id`, `wa_token`, `wa_verify`, `wa_app_secret` (com comentário pro tenant configurar no WhatsApp Business Manager).
4. **docker-compose.yml** ganha service `worker` (`command: uv run ai-sdr worker`, depends_on postgres+redis).
5. **pyproject.toml** novas deps: `arq>=0.26`, `tenacity>=8`. (`httpx` já está.)
6. **CLAUDE.md** nova seção "Messaging (Plano 5)":
   - URL shape webhooks.
   - Secrets necessários pra rodar de verdade.
   - `uv run ai-sdr worker` em dev.
   - Fluxo pending_assignment → list-pending → assign-lead → replay.
   - `WindowExpiredError` é hook Plano 9.
7. **Plano 6 prep**: `find_or_create_lead_by_address(db, tenant_id, provider, address)` introduzido na Seção 7 é literalmente a impl `InternalLead` do `IdentityResolver`. Plano 6 promove a classe + interface formal + adiciona impl Vialum. Adapter Messaging não muda.

---

## 13. Open questions

Nenhuma.

Decisões marginais que ficaram diferidas (não bloqueiam):
- Refactor `simulate` pra usar `FakeMessagingAdapter` (cosmético).
- Auto-ack pro lead pending (config futuro, default silêncio).
- Cleanup de leads `unreachable` antigos (Plano 12 polish).

---

## 14. Hooks pra planos futuros

| Plano | Hook |
|---|---|
| **6 — Identity** | Promove `find_or_create_lead_by_address` a `IdentityResolver.resolve_inbound`. Worker substitui o helper ad-hoc por chamada à interface. Adapter Messaging inalterado. |
| **8 — Media** | Adiciona `send_media`, normaliza `InboundMessage` pra suportar `media: MediaPart | None`. Aditivo, sem breaking. |
| **9 — Follow-up** | Consome `WindowExpiredError` no worker pra enfileirar template HSM. Adiciona método `send_template(to, template_name, params)` ao contrato. Scheduler dedicado (arq cron). |
| **10 — Observability** | Métricas Prometheus em `wa.send.*`, `worker.job.*`; alertas em `AuthError`/`PolicyError` recorrentes. |
| **11 — HITL UI** | Consome `GET /tenants/.../leads/pending` + `POST .../assign`. Mesma API que CLI usa hoje. |
| **Vialum-integration** | Adiciona `VialumChatAdapter` ao factory dict. Adapter-compliance suite garante drop-in compatibility. |

---

**Fim do spec.**
