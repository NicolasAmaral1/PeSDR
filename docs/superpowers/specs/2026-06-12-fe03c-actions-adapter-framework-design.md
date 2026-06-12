# FE-03c — On-Collected Actions + Adapter Framework MVP — Design

> Sub-fase 3 (final) da refatoração FlowEngine. Adiciona o pipeline genérico de **on-collected actions** + **adapter framework MVP**. FE-03a entregou objection runtime + Python validator; FE-03b entregou humanization + close lifecycle; FE-03c fecha o ciclo FE-03 com os efeitos colaterais estruturados que faltavam.

## 1. Contexto

### 1.1. Relação com FlowEngine

Spec arquitetural macro: `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md`. FE-03c implementa:

- §11 (Side-effects pipeline): "Side-effect quando campo é coletado é declarado no node YAML e disparado async pelo worker, não pelo loop conversacional."
- §27 (tenant.yaml schema): adapter registry + secrets references
- §28 (TreeFlow schema additions): `on_collected` no NodeSpec

### 1.2. Escopo travado no brainstorm (2026-06-12)

| Decisão | Escolha |
|---|---|
| Escopo da sub-fase | **MVP framework only** (sem adapters reais; só ABC + registry + factory + 1 fake) |
| Quando dispara | **Assíncrono via worker arq** (não trava run_turn) |
| Onde fica config | **Inline no NodeSpec** (`on_collected: [...]`) |
| Idempotência | **Por (Talk, field, value_hash)** com tabela `action_executions` |
| Falha | **3 retries exponencial → terminal `failed`** (sem replay automático no MVP) |

### 1.3. Posição na linha de refatoração

```
FE-01a ✅  FE-01b ✅  FE-03a ✅  FE-03b ✅  FE-03c (este)  FE-04  FE-05  FE-06  FE-07
```

FE-03c **fecha o conjunto FE-03**. Após merge, o FlowEngine v2 está completo o suficiente pra autorar tenants reais (joana-mentora) sem novos refactors estruturais.

## 2. Goals

- Permitir que TreeFlows declarem efeitos colaterais (CRM, Calendar, etc) que disparam quando um campo específico é coletado pelo LLM.
- Entregar um **framework de adapters** plug-and-play: adicionar nova integração externa não exige tocar runtime, dispatcher ou schema.
- Idempotência forte: mesmo valor coletado N vezes = 1 execução; valor diferente = nova execução (cobre correção do lead).
- Auditabilidade total: cada execução tem payload renderizado salvo em JSONB no DB.
- Async-by-default: nenhuma action externa lenta/flaky trava a conversa.

### 2.1. Non-goals (deferidos)

| Item | Fase / Plano |
|---|---|
| Triggers `on_node_enter`, `on_node_exit`, `on_close` | FE-04+ se necessário |
| Adapters reais (Google Calendar, HubSpot, Vialum CRM) | Plano dedicado de produção |
| Manual replay / re-enqueue de execuções failed | Console HITL extension |
| Alert/email/Slack em terminal failure | Plano de observability |
| Action chains / dependências entre actions | Sem caso de uso ainda |
| Conditional fire (`fire_when: "{{ ... }}"`) | YAGNI |
| Action cancellation / supersede | YAGNI |
| Voice action variants | FE-05 |

## 3. Architecture overview

FE-03c toca 5 lugares (3 módulos novos, 2 extensões pontuais):

```
┌──────────────────────────────────────────────────────────────────────┐
│                       run_turn(talk_id, inbound)                     │
└──────────────────────────────────────────────────────────────────────┘
                                  │
preprocessing → llm_call → apply_decision → dispatch_actions (NEW)
                                                    │
                                                    │ INSERT action_executions
                                                    │ + enqueue arq job
                                                    ▼
                                          ┌─────────────────────┐
                                          │ worker arq          │
                                          │ execute_action      │
                                          │  (3 retries, async) │
                                          └─────────────────────┘
                                                    │
                                          ┌─────────▼──────────┐
                                          │ ActionAdapter      │
                                          │ (logging fake, ...)│
                                          └────────────────────┘
```

### 3.1. Módulos novos

| Caminho | Propósito |
|---|---|
| `flowengine/actions/__init__.py` | Side-effect import dos adapters pra registrar no registry |
| `flowengine/actions/base.py` | ABC `ActionAdapter` + `ActionResult` dataclass |
| `flowengine/actions/registry.py` | `ACTION_ADAPTERS: dict[str, type[ActionAdapter]]` + `@register` decorator |
| `flowengine/actions/factory.py` | `build_action_adapter(name, tenant) -> ActionAdapter` |
| `flowengine/actions/dispatcher.py` | `dispatch_actions(...)` — chamado de `post_processing` |
| `flowengine/actions/templating.py` | Jinja2 sandboxed + `render_params(template, ctx)` |
| `flowengine/actions/fake.py` | `LoggingActionAdapter` (registra automaticamente) |
| `worker/jobs/execute_action.py` | arq job com retry exponencial + state machine |
| `models/action_execution.py` | ORM + ForeignKeys |
| `models/action_status.py` | `Literal[...]` + `get_args()` (single source of truth) |
| `repositories/action_execution_repository.py` | DB ops (`insert_pending`, `mark_executing`, `mark_success`, `mark_failed`) |
| `migrations/versions/0028_action_executions.py` | DDL + RLS + constraints |

### 3.2. Extensões pontuais

| Lugar | Mudança |
|---|---|
| `flowengine/treeflow_loader.py` | Parse `node.on_collected` + valida campo, adapter (warning), handler, template syntax |
| `flowengine/post_processing.py` | Adicionar `await dispatch_actions(...)` entre `apply_state_delta` e `evaluate_completion_rule` |

## 4. YAML schema

### 4.1. NodeSpec extension

Cada `NodeSpec` pode declarar `on_collected: list[OnCollectedAction]`:

```yaml
nodes:
  - id: agendamento_demo
    collect:
      - field: demo_data
        required: true
    on_collected:
      - field: demo_data          # campo gatilho (deve estar em collect)
        adapter: logging          # nome do adapter no registry
        handler: schedule_event   # método lógico
        params:                   # dict, suporta Jinja2 templating
          title: "Demo {{ collected.nome }}"
          start: "{{ collected.demo_data }}"
          duration_minutes: 30
```

### 4.2. Validação load-time (TreeFlowLoader)

| Regra | Erro |
|---|---|
| `field` precisa estar em `node.collect[].field` | Fatal: `TreeflowLoadError` |
| `adapter` não está no registry | **Warning** — pode ser registrado em runtime; fatal no dispatch |
| `handler` é string vazia ou ausente | Fatal |
| `params` é opcional, default `{}` | OK |
| `params` contém Jinja2 com syntax error | Fatal (parse no load) |
| `field` referenciado por `on_collected` mas nenhum nó nunca coleta | Warning (defensivo) |

### 4.3. Bump de version

Mudar/adicionar `on_collected` num TreeFlow já publicado **exige bump de version** (mesma regra de Plan 2). Runtime recusa re-publicar mesma version com hash diferente.

## 5. DB schema

### 5.1. Tabela `action_executions` (migration 0028)

```sql
CREATE TABLE action_executions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         UUID NOT NULL REFERENCES tenants(id),
  talk_id           UUID NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
  node_id           TEXT NOT NULL,
  field             TEXT NOT NULL,
  value_hash        TEXT NOT NULL,
  adapter_name      TEXT NOT NULL,
  handler           TEXT NOT NULL,
  params_resolved   JSONB NOT NULL,
  status            TEXT NOT NULL,
  attempts          INTEGER NOT NULL DEFAULT 0,
  last_error        TEXT,
  external_id       TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_action_executions_status
    CHECK (status IN ('pending', 'executing', 'success', 'failed')),
  CONSTRAINT uq_action_executions_dedup
    UNIQUE (talk_id, field, value_hash)
);

CREATE INDEX ix_action_executions_pending
  ON action_executions (status, created_at)
  WHERE status IN ('pending', 'executing');

CREATE INDEX ix_action_executions_tenant_talk
  ON action_executions (tenant_id, talk_id);
```

### 5.2. RLS

```sql
ALTER TABLE action_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE action_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON action_executions
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

Mesmo pattern de `talks`, `outbound_messages`, `kb_chunks`, etc.

### 5.3. Single source of truth pro enum

`src/ai_sdr/models/action_status.py`:

```python
from typing import Literal, get_args

ActionStatus = Literal["pending", "executing", "success", "failed"]
ALL_STATUSES: tuple[str, ...] = get_args(ActionStatus)
```

Migration 0028 importa `ALL_STATUSES` e monta o CHECK. Pattern já estabelecido por `talk_status.py` (FE-03b T1) e `talk_closed_by.py` (FE-03b hotfix).

### 5.4. Justificativa de design

- **`value_hash` em vez do valor cru:** JSON de qualquer tamanho cabe no UNIQUE constraint sem custo. `sha256(canonical_json(value))[:64]`.
- **`params_resolved` JSONB já renderizado:** auditoria trivial — operador vê exatamente o que foi mandado ao adapter, sem precisar re-renderizar com state histórico.
- **`external_id` nullable:** adapter `logging` retorna fake id, mas adapters futuros podem retornar `None` (ex: webhook fire-and-forget).
- **Index parcial em `pending|executing`:** acelera scan de stuck jobs sem custo em `success|failed` (a grande maioria das rows).

## 6. Action runtime

### 6.1. Dispatcher (`flowengine/actions/dispatcher.py`)

Chamado de `post_processing.apply_decision`, **depois** do merge de `collected_fields`/`extracted_facts` no estado e **antes** do close lifecycle check:

```python
# flowengine/post_processing.py — apply_decision body (extrato)
# ... merges collected + extracted_facts into state ...
await dispatch_actions(session, state, decision, talk, node_spec, lead, tenant)  # NOVO
close_outcome = evaluate_completion_rule(state, decision, treeflow)
```

Lógica do dispatcher:

```python
async def dispatch_actions(
    session, state, decision, talk, node_spec, lead, tenant,
) -> None:
    if not node_spec.on_collected:
        return

    for action_spec in node_spec.on_collected:
        if action_spec.field not in decision.collected_fields:
            continue

        value = decision.collected_fields[action_spec.field]
        value_hash = sha256_canonical_json(value)

        # Render templates (sync — pode levantar TemplateError)
        try:
            params_resolved = render_params(
                action_spec.params,
                build_template_context(state, decision, lead, talk),
            )
        except TemplateError as exc:
            logger.warning(
                "action.dispatch.template_render_failed talk=%s field=%s err=%s",
                talk.id, action_spec.field, exc,
            )
            continue

        # INSERT ... ON CONFLICT DO NOTHING (idempotency)
        execution_id = await action_repo.insert_pending(
            tenant_id=talk.tenant_id,
            talk_id=talk.id,
            node_id=node_spec.id,
            field=action_spec.field,
            value_hash=value_hash,
            adapter_name=action_spec.adapter,
            handler=action_spec.handler,
            params_resolved=params_resolved,
        )
        if execution_id is None:
            logger.info(
                "action.dispatch.skipped_duplicate talk=%s field=%s",
                talk.id, action_spec.field,
            )
            continue

        await arq_pool.enqueue_job("execute_action", str(execution_id))
        logger.info(
            "action.enqueued execution=%s adapter=%s handler=%s",
            execution_id, action_spec.adapter, action_spec.handler,
        )
```

### 6.2. Worker job (`worker/jobs/execute_action.py`)

```python
async def execute_action(ctx, execution_id_str: str) -> None:
    execution_id = UUID(execution_id_str)
    async with session_factory() as session:
        # Worker is trusted; bypass RLS for cross-tenant read.
        await session.execute(text("SET LOCAL row_security = off"))

        execution = (await session.execute(
            select(ActionExecution)
            .where(ActionExecution.id == execution_id)
            .with_for_update()
        )).scalar_one_or_none()

        if execution is None:
            logger.info("action.execution_not_found id=%s", execution_id)
            return  # Talk deleted, etc.

        # Set tenant context for any tenant-scoped reads (secrets, etc).
        await set_tenant_context(session, execution.tenant_id)

        execution.status = "executing"
        execution.attempts += 1
        await session.commit()

        try:
            tenant = await tenant_loader.load_by_id(execution.tenant_id)
            adapter = build_action_adapter(execution.adapter_name, tenant)
            result = await adapter.execute(
                handler=execution.handler,
                params=execution.params_resolved,
            )
            execution.status = "success"
            execution.external_id = result.external_id
            logger.info(
                "action.executed execution=%s attempts=%d external_id=%s",
                execution_id, execution.attempts, result.external_id,
            )
        except Exception as exc:
            execution.last_error = str(exc)[:1000]
            if execution.attempts >= 3:
                execution.status = "failed"
                logger.error(
                    "action.failed execution=%s attempts=%d err=%s",
                    execution_id, execution.attempts, exc,
                )
                await session.commit()
                return  # Terminal — não re-raise (arq não retenta).
            await session.commit()
            logger.warning(
                "action.retry execution=%s attempts=%d err=%s",
                execution_id, execution.attempts, exc,
            )
            raise  # arq re-enqueue com backoff

        await session.commit()
```

### 6.3. Retry policy

| Tentativa | Backoff (arq) | Estado durante |
|---|---|---|
| 1ª | imediato | `status=executing, attempts=1` |
| 2ª | 5s | `status=executing, attempts=2` |
| 3ª | 30s | `status=executing, attempts=3` |
| terminal | — | `status=failed, last_error=<exc>` |

Backoffs codificados em `worker/main.py` via `max_tries=3` + `defer_by` list customizada.

### 6.4. State machine

```
       enqueue
  ────────────────► pending
                       │
                       │ worker picks up
                       ▼
                   executing ──success──► success (terminal)
                       │
                       │ exception
                       ▼
                  attempts < 3 ? ──yes──► (raise → arq re-enqueue)
                       │
                       │ no
                       ▼
                    failed (terminal)
```

**Sem transição `failed → pending`** no MVP. Re-execução manual depende do plano futuro de HITL replay.

## 7. Adapter framework

### 7.1. ABC (`flowengine/actions/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from ai_sdr.schemas.tenant_yaml import TenantConfig

@dataclass
class ActionResult:
    """Returned by ActionAdapter.execute on success."""
    external_id: str | None
    detail: dict[str, Any] | None = None

class ActionAdapter(ABC):
    """Contract for FE-03c action adapters.

    Idempotency note: workers may retry an execute() call after partial
    crashes. Adapters MUST be safe to re-call — either by being idempotent
    natively, or by detecting prior execution via external system query.
    """
    name: str  # class attribute, used as registry key

    def __init__(self, tenant_config: TenantConfig, secrets: dict[str, str]):
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        ...
```

### 7.2. Registry (`flowengine/actions/registry.py`)

```python
ACTION_ADAPTERS: dict[str, type[ActionAdapter]] = {}

def register(adapter_cls: type[ActionAdapter]) -> type[ActionAdapter]:
    """Decorator: register an ActionAdapter under its `name` attribute."""
    if not getattr(adapter_cls, "name", None):
        raise ValueError(f"{adapter_cls.__name__} missing `name` class attribute")
    if adapter_cls.name in ACTION_ADAPTERS:
        raise ValueError(f"adapter {adapter_cls.name!r} already registered")
    ACTION_ADAPTERS[adapter_cls.name] = adapter_cls
    return adapter_cls
```

### 7.3. Factory (`flowengine/actions/factory.py`)

```python
class UnknownAdapterError(Exception):
    pass

def build_action_adapter(name: str, tenant: TenantConfig) -> ActionAdapter:
    if name not in ACTION_ADAPTERS:
        raise UnknownAdapterError(f"adapter {name!r} not registered")
    cls = ACTION_ADAPTERS[name]
    secrets = SopsLoader.load(tenant.slug)
    return cls(tenant_config=tenant, secrets=secrets)
```

### 7.4. Fake adapter (`flowengine/actions/fake.py`)

```python
@register
class LoggingActionAdapter(ActionAdapter):
    name = "logging"

    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        logger.info(
            "logging_adapter.executed tenant=%s handler=%s params=%s",
            self.tenant.slug, handler, params,
        )
        fake_id = f"fake-{handler}-{sha256(json.dumps(params, sort_keys=True))[:8]}"
        return ActionResult(external_id=fake_id, detail={"echo": params})
```

### 7.5. Espelho de `messaging/` — não merge

Mesmo pattern conceitual (ABC + registry + factory + fake), mas semânticas distintas:

- **MessagingAdapter:** outbound texto/template ao lead (1 método principal: `send_text`).
- **ActionAdapter:** side-effect estruturado em sistema terceiro (despacho via `handler` string).

Unificar acoplaria voz/SMS futuros a CRM/Calendar — abstração prematura.

## 8. Templating (Jinja2 sandboxed)

### 8.1. Onde renderiza

Render rola **no dispatcher (sync)**, não no worker. Razão:

| Lugar | Auditabilidade | Falha rápida | Estado fresh |
|---|---|---|---|
| **Dispatcher (escolhido)** | ✅ `params_resolved` no DB | ✅ erro = action não enfileira | ✅ state do turno atual |
| Worker | ❌ params raw + ctx perdido | ❌ falha tardia | ❌ state pode mudar |

Custo de tempo de turno: Jinja2 sandbox render é ~ms, irrelevante.

### 8.2. Contexto exposto (whitelist)

```python
def build_template_context(state, decision, lead, talk) -> dict:
    merged_collected = {**state.collected, **decision.collected_fields}
    return {
        "collected": merged_collected,
        "extracted_facts": state.extracted_facts,
        "lead": {
            "id": str(lead.id),
            "whatsapp_e164": lead.whatsapp_e164,
            "external_label": lead.external_label,
        },
        "talk": {
            "id": str(talk.id),
            "treeflow_id": talk.treeflow_id,
            "turn_count": talk.turn_count,
        },
    }
```

**Não exposto:** `lead.tenant_id` (segurança), `state.objections_handled` (PII), modelo ORM cru (evitar lazy-load acidental).

### 8.3. SandboxedEnvironment

```python
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import StrictUndefined

_env = SandboxedEnvironment(
    autoescape=False,         # JSON, não HTML
    undefined=StrictUndefined, # var ausente = UndefinedError
)
```

Builtins do Jinja2 (`upper`, `lower`, `default`, `length`, etc) ficam disponíveis. Sem filters customizados na MVP.

### 8.4. Render recursivo

```python
def render_params(template_dict, context):
    """Render strings recursively; dicts/lists traversed; scalars passthrough."""
    def walk(node):
        if isinstance(node, str):
            return _env.from_string(node).render(**context)
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node  # int, float, bool, None
    return walk(template_dict)
```

### 8.5. Brechas de templating

| ID | Cenário | Comportamento |
|---|---|---|
| T1 | Template referencia var inexistente | `UndefinedError` → action **não enfileira**, log `action.dispatch.template_render_failed`, **turno continua** |
| T2 | Template parse error | Fatal no `TreeFlowLoader` (catch no load-time) |
| T3 | Sandbox bypass tentado (`{{ ''.__class__.__mro__ }}`) | `SecurityError` do Jinja2 sandbox → bloqueado; coberto por test explícito |

## 9. Observability

### 9.1. Eventos estruturados (structlog)

| Event | Quando | Payload chave |
|---|---|---|
| `action.dispatch.skipped_duplicate` | dispatcher: UNIQUE constraint bate | `talk_id`, `field`, `value_hash` |
| `action.dispatch.template_render_failed` | dispatcher: Jinja2 UndefinedError | `talk_id`, `field`, `error` |
| `action.dispatch.unknown_adapter` | dispatcher: adapter não no registry | `talk_id`, `adapter_name` |
| `action.enqueued` | dispatcher: INSERT + enqueue OK | `execution_id`, `adapter_name`, `handler` |
| `action.executed` | worker: success | `execution_id`, `external_id`, `attempts` |
| `action.retry` | worker: exception, attempts < 3 | `execution_id`, `attempts`, `error` |
| `action.failed` | worker: terminal failure | `execution_id`, `attempts`, `last_error` |
| `action.execution_not_found` | worker: id não existe (Talk cascade-deleted) | `execution_id` |

### 9.2. Queries operacionais

Operador consulta `action_executions` direto:

```sql
-- Taxa de falha por adapter nas últimas 24h
SELECT adapter_name,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE status = 'failed') AS failed,
       COUNT(*) FILTER (WHERE status = 'failed') * 100.0 / COUNT(*) AS pct
FROM action_executions
WHERE created_at > now() - interval '1 day'
GROUP BY adapter_name;

-- Stuck jobs (provavelmente worker crash)
SELECT * FROM action_executions
WHERE status = 'executing' AND updated_at < now() - interval '5 minutes';
```

### 9.3. Sem dashboard/alert na MVP

Plano de observability dedicado entrega Grafana board + paging.

## 10. Brechas (gotchas explicitamente endereçadas)

| ID | Cenário | Mitigação |
|---|---|---|
| A1 | LLM emite campo extra que o nó não declara em `collect` | Dispatcher itera `node.on_collected`, não `decision.collected_fields`. Campo extra fica só no estado, sem ruído |
| A2 | Mesmo valor re-emitido em turno seguinte | UNIQUE `(talk_id, field, value_hash)` → INSERT falha → `action.dispatch.skipped_duplicate` |
| A3 | Lead corrige (`demo_data` muda de "quarta" pra "quinta") | `value_hash` muda → nova action; adapter recebe os dois e decide se cria/atualiza |
| A4 | TreeFlow version bumped mid-conversation com `on_collected` alterado | Cada turno carrega YAML do `treeflow_version_id` do Talk. Sem inconsistência |
| A5 | Adapter registrado mas tenant sem credencial | `SopsLoader` retorna dict sem chave → adapter levanta no `__init__` ou `execute` → retry → terminal fail com `last_error` apontando a chave |
| A6 | Worker crash mid-`execute` (kill -9, OOM) | Row fica `status=executing` indefinido; arq re-pega o job. Adapter precisa ser idempotente (documentado no ABC) |
| A7 | Múltiplos workers competindo pela mesma execution | `SELECT FOR UPDATE` no início do worker job + arq queue não duplica delivery |
| A8 | Tenant ou Talk deletado durante action pending | `ON DELETE CASCADE` derruba `action_executions`; worker faz lookup, log `action.execution_not_found`, return |
| A9 | Sandbox bypass via `{{ ... }}` malicioso | `SandboxedEnvironment` bloqueia; teste explícito de cobertura |
| A10 | Action `field` referencia campo nunca coletado | Action nunca dispara, sem erro; loader emite warning defensivo se nenhum nó coleta esse field |

## 11. Test plan (não-exaustivo — detalhamento no plan)

### 11.1. Unit

- `dispatcher`: idempotency, template error swallow, unknown adapter swallow
- `templating`: render recursivo, StrictUndefined, sandbox bypass bloqueado
- `registry`: register dup-name fails; missing-name attribute fails
- `factory`: unknown adapter raises
- `LoggingActionAdapter`: deterministic fake id
- `action_status`: `ALL_STATUSES` matches Literal exactly
- TreeFlowLoader: validação load-time (campo desconhecido fatal, parse error fatal, etc)

### 11.2. Integration

- Migration 0028 constraint accepts/rejects (pattern FE-03b T2)
- RLS isola cross-tenant em `action_executions`
- Dispatcher INSERT + enqueue arq (mock arq pool)
- Worker job: success path, retry path, terminal fail path
- E2E reference contract: turn coleta field → action enfileira → worker executa → status=success (skip-friendly via `run_turn_harness` futuro)

## 12. Out of scope (relistado pra clareza)

- Triggers além de `on_collected`
- Adapters reais (Google Calendar, HubSpot, etc)
- Replay manual de failed executions
- Alert/email em terminal failure
- Action chains, dependências, condicionais
- Voice action variants

## 13. Plan reference

Implementation plan: `docs/superpowers/plans/2026-06-12-fe03c-actions-adapter-framework.md` (será criado após aprovação deste spec via writing-plans skill).

## 14. Version bump

CLAUDE.md ganha seção "Actions (FE-03c)" descrevendo: YAML schema, runtime, adapter framework, registry pattern, observability events, troubleshooting.
