# PeSDR — Claude Code Instructions

## Project context

Multi-tenant AI SDR platform. Full design: `docs/superpowers/specs/2026-05-21-ai-sdr-design.md`.
Implementation plans: `docs/superpowers/plans/`.

Pilot tenant: mentora de marca pessoal (2 funis: Mentoria R$ 6.000 e Aceleradora R$ 1.497–2.000, + downsell R$ 247).

## Tech stack

Python 3.12 · uv · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · Postgres 16 + pgvector · Redis · structlog · LangGraph + langgraph-checkpoint-postgres (psycopg3) · LangChain (anthropic + openai) · simpleeval · typer · pytest · ruff · mypy · SOPS + age.

## Workflow

- TDD: write failing test, implement minimum, refactor.
- Commit per task. Reference plan task in commit message ("Plan N Task M").
- Run `make lint && make format && make type && make test-unit` before commit.
- Integration tests need `make up` (docker compose).

## Multi-tenancy (CRÍTICO)

- Todo tenant-scoped table tem `tenant_id UUID` + Row-Level Security policy.
- App conecta como `ai_sdr_app` (NOSUPERUSER), porque superusers bypassam RLS.
- Set tenant per-transaction via `await set_tenant_context(session, tenant_id)` (usa `set_config()`, não `SET LOCAL`).
- Ver `src/ai_sdr/db/rls.py`.

## Secrets

- NEVER commitar plaintext secrets.
- Secrets de tenant em `tenants/<id>/secrets.enc.yaml` (SOPS-encrypted, age).
- Decrypt via `SopsLoader.load(tenant_id)`.
- Public key da VPS está em `.sops.yaml`. Pra adicionar dev local, peça a public key dele e adicione como recipient.

## Tenant config

- Cada tenant tem `tenants/<id>/tenant.yaml` (validado por `ai_sdr.schemas.tenant_yaml.TenantConfig`).
- Load via `TenantLoader.load(tenant_id)`.

## Database conventions

- Migrations em `migrations/versions/NNNN_*.py` (deterministic revision IDs).
- Asyncpg gotchas:
  - **Não aceita múltiplas statements** numa única `execute()` — divida.
  - **`SET LOCAL` não aceita parâmetros** — use `SELECT set_config(name, value, is_local)`.

## Adding a new tenant

1. Crie `tenants/<slug>/tenant.yaml`.
2. Crie `tenants/<slug>/secrets.enc.yaml` (na VPS):
   ```bash
   cat > tenants/<slug>/secrets.enc.yaml <<EOF
   anthropic_key: "<value>"
   ...
   EOF
   sops --encrypt --in-place tenants/<slug>/secrets.enc.yaml
   ```
3. Insira row em `tenants` table (via psql, comando dedicado virá em plano futuro):
   ```sql
   INSERT INTO tenants (slug, display_name) VALUES ('<slug>', '<Display Name>');
   ```

## Ports na VPS

- Postgres: `15432` (host) → `5432` (container)
- Redis: `16379` → `6379`
- API: `8200` (futuramente atrás de Traefik)

## TreeFlow authoring (Plan 2)

- TreeFlow YAMLs ficam em `tenants/<slug>/treeflows/<id>.yaml`. Schema em `ai_sdr.schemas.treeflow_yaml.TreeFlow`.
- Validar local: `uv run python -c "from pathlib import Path; from ai_sdr.treeflow.loader import TreeFlowLoader; TreeFlowLoader(Path('tenants')).load('<slug>', '<id>')"`.
- Bump de `version` (semver) é obrigatório pra mudar o YAML — runtime recusa re-publicar mesma versão com hash diferente.
- Transition/exit expressions usam `simpleeval`: comparações, `and/or/not`, `in`, `is_set('field')`, literais, `true`/`false`. Sem function calls (exceto `is_set`), sem attribute access, sem dunders.
- Exit conditions:
  - `all_fields_filled` — todos os `collects[].required` presentes e não None
  - `rule_expression` — `expression: "<expr>"` avaliada contra `collected`
  - `combined` — ambos
- Forward-compat fields num NodeSpec (aceitos, ignorados em Plan 2): `knowledge_base` (Plan 3), `handles_objections` (Plan 4), `sync_to_crm` (Plan 5), `critical` (Plan 3).

## TalkFlow runtime

- API: `ai_sdr.treeflow.runtime.TalkFlowRuntime` — `publish_version` / `create` / `step`.
- `thread_id = f"{tenant_id}:{talkflow_id}"` — LangGraph checkpointer chaveia em `thread_id`; isolamento real vem de (a) RLS na tabela `talkflows`, (b) prefixo enforced por `create()`.
- Uma LLM call por `.step()`. Sem retry/backoff (Plan 8).

## Simulate CLI

```bash
# 1. Inserir tenant na DB (uma vez)
docker exec -it ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "INSERT INTO tenants (slug, display_name) VALUES ('example', 'Example');"

# 2. Garantir que tenants/example/secrets.enc.yaml tem anthropic_key real

# 3. Rodar
uv run ai-sdr simulate --tenant example --treeflow example --lead test-1 --show-extracted
# Enter na 1ª prompt → agente cumprimenta. /quit sai, /restart apaga e reinicia.
```

## KB (Plan 3)

- Files: `kb/<tenant>/<kb_id>/*.md`. Each `## heading` is a chunk; chunks > 600 tok split by paragraph (or sentence as a fallback). Encoder: tiktoken `cl100k_base`.
- Reindex: `uv run ai-sdr reindex-kb --tenant <slug> [--kb <id>] [--prune] [--kb-root path]`. Idempotent via sha256(content) — only changed docs re-embed. Default `--kb-root` is `kb/` relative to CWD.
- Embedding: OpenAI `text-embedding-3-small` (1536d). Config in `tenant.yaml > llm.embeddings`. Requires `openai_key` in `secrets.enc.yaml`.
- Retrieval: per-Node `knowledge_base: [{id, top_k, min_score}]`. Multiple refs aggregate into one SQL with `kb_id = ANY(...)`. Filtering by `min_score` happens in Python after the SQL `ORDER BY embedding <=> $q LIMIT max(top_k)`.
- pgvector index: IVFFlat `lists=100` (good for <10k chunks). To rebuild after large KB growth: `REINDEX INDEX ix_kb_chunks_embedding;` (or drop + recreate with larger `lists`).
- Cross-tenant isolation: RLS via `tenant_id` (FORCE) — same pattern as `talkflows`. Always set via `set_tenant_context(session, tenant.id)`.
- Indexer commit policy: `reindex_tenant_kb` does NOT commit — callers must wrap in `async with session.begin():`.

## Guardrails (Plan 3)

- Config: `tenant.yaml > guardrails` block — `enabled`, `allowed_prices: list[int]`, `allowed_products: list[str]`, `critic_enabled`, `fallback_text` (≥10 chars), `max_retries` (1–5, default 2). If `enabled=true` you MUST set at least one allowlist; validator rejects empty.
- Pipeline (post-LLM): `validate_whitelist` → if `node.critical=True` and `critic_enabled=True`, `critic_pass` (Haiku via `tenant.llm.classifier`). On Verdict fail: prepend `SystemMessage(suggested_fix)`, retry. After `max_retries`, fallback text emitted; `collected={}` (conversation stays on the same node).
- LLM is asked to emit `prices_mentioned: list[int]` + `products_mentioned: list[str]` as part of its structured output — the validator compares those lists, NOT regex on `response_text`. Field instructions tell the LLM to enumerate everything it mentioned textually.
- Kill switch: `tenant.guardrails.enabled=false` makes the runner a passthrough (whitelist and critic both no-op).
- HITL future: `guardrails/runner.py:_handle_exhausted()` is the single hook to swap when Plan-N adds human-in-the-loop. Its current body (return fallback text) becomes `await persist_pending_review(...); raise GraphInterrupt()`.

## Objection Classifier (Plan 4a)

- Tenant config: `tenant.yaml > objections` block — `enabled` (default `true`), `min_confidence` (default `0.6`), `max_handled_per_lead` (default `10`), `history_window` (default `4`). Section is optional; defaults preserve enabled=true.
- Per-objection schema: every `NodeObjection` / `GlobalObjection` requires `id`, `kb`, `description` (10-300 chars). The description is what the classifier sees — be specific in PT-BR. `as_subnode: <node_id>` is optional; when set, the classifier dispatches to the referenced full Node (which must declare a transition to `BACK_TO_ORIGIN`).
- Reuses `tenant.llm.classifier` (Haiku) — no new LLM config needed.
- Topology: compiler emits synthetic LangGraph nodes `{node_id}__classifier` and (when N has inline objections) `{node_id}__inline`. Double-underscore separator avoids the LangGraph-reserved chars `:` and `|`. `state.current_node` stays as the TreeFlow node id (never the synthetic names). Downstream code that needs the suffix MUST import `CLASSIFIER_SUFFIX` / `INLINE_SUFFIX` from `ai_sdr.treeflow.compiler` rather than hardcoding.
- Kill switch: `tenant.objections.enabled=false` makes every `__classifier` a passthrough (zero Haiku call). Same pattern as the guardrails kill switch.
- CLI: `ai-sdr simulate ... --no-classifier` to disable for a single run (debug); `--show-extracted` prints `objections_handled` records per turn.
- Failure modes (all degrade to "no match → main", never block the turn): Haiku raise (rate limit / network / auth), structured-output validation error, hallucinated objection_id, KB empty, KB missing, `BACK_TO_ORIGIN` with no origin (falls back to entry_node).
- Events emitted (structlog): `objection.classifier.{skipped,detected,no_match,error,invalid_output,hallucinated_id}`, `objection.inline.responded`, `objection.subnode.{entered,exited,orphan_return}`, `objection.kb.{empty,missing}`, `objection.threshold.exceeded`, `objection.inline.rehydrate_failed`.
- TreeFlow version bump required when adding objections to an existing TreeFlow YAML (runtime refuses to re-publish same version with different hash — Plan 2 rule).
- Sub-node mode: a `NodeSpec` referenced by `as_subnode` must include a transition with `target: "BACK_TO_ORIGIN"`. The schema validator emits a warning when a node uses `BACK_TO_ORIGIN` but no objection references it (likely authoring mistake).
- State extension: `TalkFlowState.objections_handled: list[ObjectionRecord]` (append-only via `operator.add` reducer). Each record has `objection_id`, `detected_at_node`, `turn_index`, `quote`. Survives checkpoints; cross-turn.

## Prompt caching (Anthropic, Plan 3)

- `tenant.llm.cache_enabled: bool` (default `true`). Applies to Anthropic only — OpenAI auto-caches prefixes ≥1024 tok and exposes no disable.
- Structure per turn: `SystemMessage(content=[{static_prompt, cache_control: ephemeral}, {kb_block}])`. The static block caches; the KB block doesn't (it's dynamic per turn).
- Tools (the structured-output schema) are part of the cacheable prefix automatically.
- Min cacheable: ~1024 tok. Below that, `cache_control` is silently ignored by the provider — `TreeFlowLoader` warns at load time via `treeflow.cache_below_threshold`.

## Multi-provider LLM (Plan 3 T2b architectural opening)

- `tenant.llm.default.provider` is now free-form `str` (was `Literal["anthropic", "openai"]`). `build_llm` dispatches via `langchain.chat_models.init_chat_model("<provider>:<model>", api_key=..., temperature=..., max_tokens=...)`.
- Installed provider deps (after T2b): `langchain-anthropic`, `langchain-openai`, `langchain-google-genai`, `langchain-deepseek`, `langchain-ollama`. To add another (Bedrock/VertexAI/Mistral/etc.), add the package + nothing else — `init_chat_model` will resolve it.
- End-to-end validation per provider (live tests, prompt caching tuning, error shape handling) is Plan 4.
- `secrets/` prefix convention: tenant.yaml's `api_key_ref` must start with `secrets/` (enforced by validator). At lookup time, factory strips the prefix and does `secrets[bare_name]`. SopsLoader returns secrets keyed by bare names.

## FE-03a — Objection Runtime + Python Validator

Substitui o Plano 4a (objection classifier v1) na linha FlowEngine v2.

- **YAML:** `global_objections[]` + `nodes[].handles_objections[]` com `treatment_mode: tool | inline`. `tool_payload` exige `max_treatment_turns ∈ [1,10]`, `canonical_arguments_summary`, `kb_ref`, `resolution_criteria`, `on_max_turns_no_resolution.action ∈ {gracefully_continue, escalate_to_human}`. Bounds errors são fatais — tenant nem inicia.
- **Detecção:** LLM principal emite `TurnDecision.detected_objection`. Inline mode é resolvido no `response_text` direto; tool mode entra em `TalkFlowState.active_treatment`.
- **Resolução:** LLM emite `treatment_status: in_progress | resolved_accepted | resolved_deferred`. Conservative guidance no system prompt instrui a preferir deferred em dúvida.
- **Cross-objection:** nova objeção tool durante tratamento ativo defere a anterior automaticamente.
- **Max turns:** ao esgotar, executa `on_max_turns_no_resolution.action` — `gracefully_continue` limpa estado; `escalate_to_human` adicionalmente seta `Talk.requires_review_reason='objection_treatment_exhausted'`.
- **Validador Python:** `validate_response_text` ganha `allowed_products` + normalização. Violação dispara 1 retry corretivo; segunda violação envia `tenant.guardrails.fallback_text` + `Talk.requires_review_reason='validator_exhausted'`.
- **Tenant.yaml:** `guardrails.allowed_products` + `guardrails.fallback_text` (>=10 chars) são obrigatórios quando `enabled=true`.
- **Routing:** simpleeval context expandido — YAML pode referenciar `extracted_facts`, `objections_handled`, `turn_index`. Bloqueia transição quando `active_treatment` setado (failure_reason `transition_blocked_by_treatment`, reusa corrective retry).
- **Brechas conversacionais:**
  - Off-topic: `TurnDecision.off_topic_detected` flag + counter persisted as shadow key `__off_topic_count__` inside `TalkFlowState.collected`; aos 3 escalates com `requires_review_reason='off_topic_exhausted'`.
  - Lead pede humano: LLM emite `request_human_escalation` (qualquer category); runtime seta `requires_review_reason='escalation_requested'`.
  - Mídia (áudio/imagem): **gap conhecido**, depende FE-05. Tenant configura Meta Business Manager pra não receber mídia ANTES de subir FE-03a sem FE-05.
- **Brechas técnicas:** transação única no `run_turn`; worker concatena inbounds pendentes (janela 2s configurável via `WORKER_INBOUND_CONCAT_WINDOW_SECONDS`); TreeFlow snapshot at Talk open (versão sumiu → `requires_review` com `treeflow_version_missing` — handler vive no worker, não em preprocessing).
- **Heurísticas pós-LLM:** contradição (`accepted` → `deferred` quando texto contradiz) e implicit transition (event-only).
- **Migration 0025:** `talks.requires_review_reason` enum com 5 valores. Source-of-truth Literal em `ai_sdr.models.review_reason.RequiresReviewReason`. Consumido pelo HITL console (FE-07).

### FE-03a config no tenant.yaml

```yaml
guardrails:
  enabled: true
  disallowed_price_pattern: "R\\$\\s*\\d+"
  allowed_prices: ["R$ 6000", "R$ 2000", "R$ 1497", "R$ 247"]
  allowed_products: ["Mentoria", "Aceleradora"]
  fallback_text: "Deixa eu confirmar isso com a equipe, te retorno em alguns minutos."
```

### TreeFlow YAML — exemplo objection block

```yaml
global_objections:
  - id: preco
    description: "lead questiona valor, acha caro"
    treatment_mode: tool
    tool_payload:
      canonical_arguments_summary: "ROI cabe em 1 mês com volume X"
      kb_ref: argumentos_preco
      max_treatment_turns: 3
      resolution_criteria: "lead aceitou parcelamento ou pediu próximo passo"
      on_max_turns_no_resolution:
        action: gracefully_continue
        message_hint: "Reconheça hesitação, ofereça material"
```

### Wipe pra dev fresh (atualiza FE-01a guidance)

```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations; \
      UPDATE talks SET status='active', requires_review_reason=NULL, escalated_at=NULL;"
```

## FE-03b — Humanização + Close Lifecycle

Polish do runtime que FE-03a entregou.

### Humanização

- `humanization` block em `tenant.yaml`: `enabled` (default true), `chunk_delimiter` (default `\n\n`), `chars_per_second_min/max` (8/15), `min_delay_ms`/`max_delay_ms` (800/4000), `apply_to_voice` (false).
- **Pipeline:** `humanize(response_text, config, *, is_voice)` em `flowengine/humanizer.py` é pure function — split por parágrafo + computa delay proporcional ao próximo chunk com bounds.
- **Sender** (`flowengine/sender.py`) itera chunks: `mark_as_typing(to)` (opcional, no-op default no protocol), `asyncio.sleep`, `send_text`.
- **WhatsApp Cloud** implementa `mark_as_typing` via `typing_indicator` API; PolicyError silenciado pq Meta gates per account.
- **Voice mode**: humanização pulada (1 chunk) unless `apply_to_voice=true`. FE-05 wire chunking diferente.

### Close lifecycle

- `talk_lifecycle` block opcional no TreeFlow YAML: `close_after_inactivity` (ISO-8601, [PT1H, P365D]), `close_after_duration` (ISO-8601, [P1D, P730D]), `close_when_completed: [{ expression, outcome }]` (outcome ∈ {success, failure, no_interest}).
- **Inactivity + Duration**: worker scan job (`worker/jobs/scan_talks.py`) roda cron a cada 5 minutos, cross-tenant via BYPASSRLS + SKIP LOCKED. **Two-phase**: read candidates → per-Talk fresh tx (SET LOCAL row_security=off → SELECT FOR UPDATE SKIP LOCKED → close → commit). Crash mid-batch preserva closures anteriores.
- **Completion rule**: pipeline hook em `post_processing.apply_decision` após state delta. **Mutually exclusive com requires_review_reason** (close vence; review skipped).
- **Re-engagement**: lead manda mensagem após Talk close → **nova Talk fresca** (não reopen). preprocessing emite `talk.re_engagement_after_close` event.
- **Bounds errors fatais**: TreeFlow com talk_lifecycle inválido → `TreeflowLoadError`. Tenant nem inicia.

### Migration 0026

Estende `talks.status` CHECK constraint pra incluir `closed_completed_success`, `closed_completed_failure`, `closed_no_interest`, `closed_duration`. Source-of-truth em `ai_sdr.models.talk_status.TalkStatus` Literal + `ALL_STATUSES` tuple — migration e ORM importam de lá (pattern de `review_reason.py` em FE-03a).

### Eventos structlog (9 novos)

`talk.closed.{inactivity,duration,completion}`, `talk.re_engagement_after_close`, `humanization.{chunks_emitted,skipped_voice_mode}`, `mark_as_typing.{unsupported,failed}`, `scan_talks.completed`.

### Wipe pra dev fresh (atualiza FE-03a guidance)

```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations; \
      UPDATE talks SET status='active', requires_review_reason=NULL, escalated_at=NULL, \
                       closed_at=NULL, closed_reason=NULL, closed_by=NULL;"
```

## FE-03c — On-Collected Actions + Adapter Framework

- TreeFlow YAMLs declaram side-effects inline em `TreeflowNode.on_collected`:
  ```yaml
  nodes:
    - id: agendamento_demo
      collects:
        - field: demo_data
          type: text
          required: true
      on_collected:
        - field: demo_data
          adapter: logging
          handler: schedule_event
          params:
            title: "Demo {{ collected.nome }}"
            duration_minutes: 30
  ```
- Action dispara **assíncrono via worker arq** depois que LLM emite `collected_fields[field]`. Não bloqueia run_turn. Dispatch acontece em `post_processing.apply_decision` antes do current_node update (usa o nó pré-transição como contexto).
- **Idempotência**: UNIQUE `(talk_id, field, value_hash)` em `action_executions` — mesma coleta (mesmo valor) = skip; correção (valor muda) = nova action. Hash = `sha256(canonical_json(value))`.
- **Templating**: Jinja2 SandboxedEnvironment com `StrictUndefined`. Contexto exposto: `collected`, `extracted_facts`, `lead.{id, whatsapp_e164, external_label}`, `talk.{id, treeflow_id, turn_count}`. `tenant_id` é deliberadamente **não exposto**. Render rola no dispatcher (sync), `params_resolved` no DB já é o final — auditoria trivial.
- **Adapter framework**: ABC `ActionAdapter` + registry (`@register` decorator) + factory (`build_action_adapter`). Adicionar novo adapter:
  1. Subclasse `ActionAdapter`, set `name`, implementa `execute(handler, params)`.
  2. Decora com `@register` (registry rejeita nome duplicado).
  3. Importa o módulo em `src/ai_sdr/flowengine/actions/__init__.py` (side-effect).
- **Adapters incluídos no MVP**: `logging` (fake/test, retorna fake id determinístico `fake-{handler}-{sha256[:8]}`). Adapters reais (Google Calendar, HubSpot, etc) ficam pra plano dedicado de produção.
- **Falha**: 3 retries com backoff exponencial (5s, 30s — gerenciado pelo arq via `WorkerSettings.max_tries`). Após terminal: `status='failed'`, `last_error` (truncado a 1000 chars). Sem replay automático no MVP — operador investiga via SQL e abre plano se for sistêmico.
- **Bump de version** obrigatório ao adicionar/mudar `on_collected` num TreeFlow já publicado (Plan 2 rule).
- **Events estruturados** (structlog): `action.enqueued`, `action.executed`, `action.retry`, `action.failed`, `action.dispatch.skipped_duplicate`, `action.dispatch.template_render_failed`, `action.execution_not_found`.
- **Validação load-time**: TreeflowLoader rejeita `field` inválido (não declarado em `collects`), `handler` vazio, `params` com sintaxe Jinja2 inválida. Adapter ausente do registry emite warning (não falha) — pode ser registrado em runtime.
- **Queries operacionais**:
  ```sql
  -- Taxa de falha por adapter (24h)
  SELECT adapter_name,
         COUNT(*) FILTER (WHERE status='failed') * 100.0 / COUNT(*) AS pct_failed
  FROM action_executions
  WHERE created_at > now() - interval '1 day'
  GROUP BY adapter_name;

  -- Stuck jobs (worker crash?)
  SELECT * FROM action_executions
  WHERE status='executing' AND updated_at < now() - interval '5 minutes';
  ```
- **Cross-tenant worker**: `execute_action` faz `SET LOCAL row_security = off` pra lookup, depois `set_tenant_context` pra reads tenant-scoped (Tenant.slug → tenant.yaml + secrets via SopsLoader).
- **Wipe pra dev fresh**: `docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "TRUNCATE action_executions;"`.

## Checkpointer notes

- Tabelas do LangGraph (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) são criadas pelo `ensure_checkpointer_schema()` no startup (chamado no lifespan da FastAPI e no `ai-sdr simulate`). Migration 0004 é só um stamp documental — NÃO cria as tabelas (a lib usa psycopg3, alembic env usa asyncpg).
- Tabelas do checkpointer NÃO têm `tenant_id` nem RLS. Isolamento via:
  1. `thread_id` sempre prefixado com `tenant_id:` (enforced por `TalkFlowRuntime.create`)
  2. RLS em `talkflows` (lookup `talkflow_id → thread_id`)
- Wipe pra dev fresh: `docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations;"`

## Messaging (Plano 5)

- Adapter contract: `src/ai_sdr/messaging/base.py` (`MessagingAdapter` ABC + `InboundMessage`/`SendResult` dataclasses).
- Default standalone impl: `whatsapp_cloud` (`whatsapp_cloud.py`). Fake impl for dev/tests: `fake.py`.
- Choose impl via `tenant.yaml > messaging.provider`. For `whatsapp_cloud`, set the four `*_ref` fields (all under the `secrets/` prefix convention).
- Webhook URLs: `https://<host>/webhooks/<tenant_slug>/<provider>`. GET = handshake (WhatsApp `hub.mode=subscribe`); POST = ingestion.
- Idempotency: dedupe via UNIQUE `(tenant_id, provider, external_id)` on `inbound_messages`. Repeated webhooks = no-op insert.
- Worker (`uv run ai-sdr worker`, or the `worker` docker-compose service in prod): consumes `process_lead_inbox` jobs from the Redis queue. Serialization per-lead via `pg_advisory_lock`. **Always run the worker in production** — the API does not process inbounds.
- Bootstrap (HITL-friendly): a brand-new lead nasce `status='pending_assignment'`. Mensagens ficam queued no DB; **nada acontece** até operador atribuir treeflow via:
  - `ai-sdr leads list-pending --tenant <slug>` (lista)
  - `ai-sdr leads assign-lead --tenant <slug> --lead <uuid> --treeflow <id>` (atribui)
  - `POST /tenants/<slug>/leads/<uuid>/assign {treeflow_id}` (REST)
- Replay-all: ao atribuir, o worker processa todas as inbounds acumuladas em `received_at ASC`.
- Erros tipados (`messaging/errors.py`):
  - `RecipientUnreachable` → marca `lead.status='unreachable'`; worker para.
  - `WindowExpiredError` → marca msg como `error`; **hook do Plano 9** (template HSM).
  - `AuthError`, `PolicyError` → log + alert; worker para (sem retry — precisa de operador).
  - `TransientError` / `RateLimitError` (429) → adapter resolve internamente via `tenacity` (3 tentativas, backoff exponencial, respeita `Retry-After`).
- Adapter compliance: `tests/integration/test_adapter_compliance.py` é parametrizado por impl — qualquer novo adapter (Vialum Chat etc.) entra apenas adicionando ao `params`.

### Adding a new tenant's WhatsApp config

1. No painel Meta Business Manager: obtenha `phone_number_id`, gere um system-user access token de longa duração, configure o webhook URL (`/webhooks/<slug>/whatsapp_cloud`) com um `verify_token` que você escolhe, e copie o **App Secret** da Meta App.
2. Em `tenants/<slug>/secrets.enc.yaml` (via SOPS): salve `wa_phone_id`, `wa_token`, `wa_verify`, `wa_app_secret`.
3. Em `tenants/<slug>/tenant.yaml`: defina o bloco `messaging:` apontando pra `whatsapp_cloud` com as 4 *_ref.
4. Restart da API (re-carrega `tenant.yaml`) e do worker.

### Simulator vs worker

- `ai-sdr simulate` continua sendo dev tool — NÃO usa adapter de WhatsApp. Cria/reusa um Lead por `external_label`, marca como `status='active'` automaticamente.
- Em produção: NUNCA rode `simulate` apontando pra tenant real; use `worker` + webhook.

## HITL Console (Plano 11)

- Operator console at `/console/{tenant_slug}/leads`. Stack: FastAPI + Jinja2 + HTMX (no build step, no new container).
- Per-tenant enable: `tenant.yaml > console.enabled: true`. Default `false` (block omitted or explicitly false) returns 404 on the console URLs.
- Credentials in `users` table (NOT in tenant.yaml). Schema: `users(id, username, password_hash, is_platform_admin, ...)` + `user_tenant_access(user_id, tenant_id, role)`. Both global (no RLS — they serve the auth mechanism).
- Auth: signed cookie (`pesdr_session`) via `itsdangerous` URLSafeTimedSerializer with `CONSOLE_SECRET_KEY` env var. 12h sliding expiration. Cookie scoped to `/console`.
- RBAC: operator with grant accesses their tenant; `is_platform_admin=true` bypasses the grant check.
- Provisioning via CLI:
  - `ai-sdr users add --username X [--admin] [--password ...]` (prompts password if absent)
  - `ai-sdr users grant --username X --tenant slug --role operator`
  - `ai-sdr users revoke --username X --tenant slug`
  - `ai-sdr users passwd --username X` (prompts new password)
  - `ai-sdr users list [--tenant slug]`
  - `ai-sdr users set-admin --username X --admin true|false`
- Polling: master list re-fetches every 10s via HTMX `hx-trigger="every 10s"`. Assign POST returns the updated master list + an OOB swap that resets the detail panel.
- Provider-agnostic display: lead identifier is `whatsapp_e164` formatted, else `external_label`, else `#<id[:8]>`. Works for Vialum Chat tenants in the future without code changes.
- Vialum tenants: set `console.enabled: false` and use Vialum Tasks Inbox as the HITL surface.
- Treeflow enumeration for the dropdown: filesystem-based (`tenants/<slug>/treeflows/*.yaml` filenames). Not a tenant.yaml field.
- ENV var required when any tenant has `console.enabled: true`:
  ```
  CONSOLE_SECRET_KEY=<32+ chars random>  # python -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- Local smoke:
  1. `ai-sdr users add --username joana` (set a password)
  2. `ai-sdr users grant --username joana --tenant example --role operator`
  3. Open `http://localhost:8200/console/login`, log in, get redirected to `/console/example/leads`.

- **Template params**: rendered with Jinja2 `SandboxedEnvironment` against:
  - `collected.<field>` — TalkFlow's extracted fields (v1 passes `{}` — full LangGraph state lookup wiring may come in P10)
  - `lead.whatsapp_e164`, `lead.external_label`
  - `tenant.slug`, `tenant.display_name`
  - Filters: `default`, `lower`, `upper`, `trim`, `truncate(N)`. `StrictUndefined` forces explicit defaults.

- **Schedule semantics**: timer starts at `talkflow.last_agent_message_at`. Lead inbound resets counter + cancels pending + reactivates cold. Scanner runs every 60s; per-lead `pg_advisory_lock` (same hash as `process_lead_inbox`) serializes scanner vs worker. Race-belt at fire time checks `talkflow.last_lead_message_at > job.scheduled_at`.

- **Schedule-one-at-a-time**: each fired job inserts the next attempt's row. Config changes in `treeflow.yaml` apply to subsequent in-flight schedules naturally. Requires bumping the TreeFlow `version` to publish a new content_hash.

- **CLI ops**:
  ```bash
  ai-sdr follow-ups list --tenant <slug> [--lead <uuid>] [--status pending|completed|cancelled|error|all]
  ai-sdr follow-ups cancel --tenant <slug> --lead <uuid>
  ai-sdr follow-ups dry-run --tenant <slug> --treeflow <id> --lead <uuid>
  ```

- **Cold lead reactivation**: a `talkflow.status='cold'` lead that receives an inbound is automatically flipped back to `'active'` by `process_lead_inbox`; attempt counter resets to 0; new follow-up scheduled after agent's reply.

- **WhatsApp HSM payload**: Meta API endpoint `POST /messages` with `type=template`. Body params are positional (`{{1}}, {{2}}, ...` in the Meta-registered template), filled from `params` list at send time. Same retry stack (tenacity 3 attempts, exp backoff) and error classification (`_classify_error`) as `send_text`.

- **Migration**: `0010_follow_up_and_talkflow_columns` — `follow_up_jobs` table (RLS, partial indexes) + 3 columns on `talkflows`.

- **Setting up a tenant for live follow-up**:
  1. Register HSM templates in Meta Business Manager. Note the exact `name` strings.
  2. Edit `tenants/<slug>/treeflows/<id>.yaml`: add the `follow_up:` block with matching `template_ref`s. Bump `version` (semver).
  3. (Optional) Edit `tenants/<slug>/tenant.yaml` `messaging.reengagement_template` for WindowExpired recovery.
  4. Restart worker: `docker compose up -d --build worker`. The cron registers on startup.

## Observability (Plano 10)

### LangSmith tracing

Opt-in via 3 env vars (in `.env`, or in the VPS environment):

```bash
LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=ls__...                  # from https://smith.langchain.com
LANGCHAIN_PROJECT=pesdr-prod                # or pesdr-dev locally
```

When set, langchain-core auto-traces every chain run from the 4 LLM call sites:
- `runtime.graph.ainvoke` (trace_origin=`process_lead_inbox`)
- `classifier.structured.ainvoke` (trace_origin=`objection_classifier`)
- `extractor.runnable.ainvoke` (trace_origin=`field_extractor`)
- `critic.runnable.ainvoke` (trace_origin=`guardrails_critic`)

Each trace carries metadata: `{tenant_id, tenant_slug, talkflow_id, lead_id, node, turn_index, trace_origin}`.

**Filter examples in the LangSmith dashboard:**
- All traces for Joana: `metadata.tenant_slug = "joana"`
- All critic passes: `metadata.trace_origin = "guardrails_critic"`
- Traces for a specific lead: `metadata.lead_id = "uuid"`
- Slow turns: `latency > 10s` + filter by metadata

**Without `LANGSMITH_API_KEY`** but with `LANGCHAIN_TRACING_V2=true`: langchain silently no-ops; the app boots a structlog warning at startup so the operator notices.

**Sampling:** 100% in v1. If volume exceeds free tier (5k traces/mo), add sampling via a future plan.

### Outbound audit (`outbound_messages` table)

Every adapter send is persisted with full context — `body_text` or `template_ref + template_params`, `status` (sent/failed), `error_detail`, `triggered_by` (inbound | follow_up_scanner | window_expired_recovery), and FKs to the source `inbound_message` or `follow_up_job`.

Query via CLI:
```bash
ai-sdr outbound list --tenant <slug>                              # last 50, all statuses
ai-sdr outbound list --tenant <slug> --status failed              # only failures
ai-sdr outbound list --tenant <slug> --lead <uuid>                # history of one lead
ai-sdr outbound list --tenant <slug> --status sent --limit 200    # more rows
```

Or directly in `psql`:
```sql
SELECT sent_at, message_type, status, body_text, template_ref, error_detail, triggered_by
FROM outbound_messages
WHERE tenant_id = '<uuid>' AND lead_id = '<uuid>'
ORDER BY sent_at DESC LIMIT 50;
```

(Both methods require `set_tenant_context()` to be set if hitting via the app role; `psql` as superuser bypasses RLS.)

### Known race

When `adapter.send_*` succeeds but the worker's `db.commit()` then fails (DB hiccup, etc.), the message went out to Meta but the audit row is lost. The worker emits `log.warning("outbound.audit_lost", external_id=..., ...)` with enough payload to reconstruct manually. No automatic retry — Meta's `external_id` can't be duplicated without double-sending. 2-phase outbox pattern is a future plan if this becomes operational pain.

### What's NOT here

- Prometheus / Grafana / OTel — defer until volume justifies (multi-customer scale).
- Alerts / paging — log structured serves; alert routing is a future plan.
- Cost dashboard custom — LangSmith UI already reports tokens + cost per provider.
- Trace of DB queries / arq jobs — only LLM calls are traced. Add via `@traceable` decorator in a future plan if needed.
