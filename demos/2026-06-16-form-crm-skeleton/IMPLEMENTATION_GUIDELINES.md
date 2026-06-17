# Implementation Guidelines

> Padrões a seguir ao migrar os stubs deste skeleton pra `src/ai_sdr/` real.

Consolidação dos padrões já estabelecidos pelo Nicolas no codebase (FE-03c, Plano 5, Plano 9, etc) + algumas novas convenções específicas pra Form + CRM.

## 1. TDD por task (CLAUDE.md)

**Regra de ouro:** cada commit é "1 task = 1 PR-mini = 1 ciclo TDD".

```
write failing test → implement minimum → refactor → commit
```

- Testes ficam em `tests/unit/` e `tests/integration/` (não em pastas dos módulos)
- Nome do arquivo: `test_<modulo>.py` (snake_case)
- 1 commit por test+impl. Não acumular múltiplas tasks num PR.
- Mensagem do commit referencia a task da spec: `feat(forms): A5 — RespondiFormAdapter parser + signature validation`

## 2. Type safety (mypy strict)

Todos os arquivos novos passam por `mypy src --strict`.

- Type hints em **todos** os parâmetros e retornos públicos
- `dict[str, Any]` é último recurso — prefira Pydantic models
- `Literal[...]` pra enums (`DealStage`, `ActionStatus`, `TalkStatus`)
- `TypedDict` pra payloads JSON específicos (e.g., `RespondiPayload`)

## 3. RLS (multi-tenant)

**Toda query em tabela tenant-scoped exige `set_tenant_context()` antes:**

```python
async with session.begin():
    await set_tenant_context(session, tenant.id)
    # ... queries aqui ...
```

Esquecer = vazamento cross-tenant (incidente LGPD potencial).

Tabelas tenant-scoped nesta spec:
- `inbound_form_submissions` (RLS via tenant_id)
- `action_executions` (já existe — FE-03c)
- `leads.crm_refs` (já está em `leads` que tem RLS)

**Worker bypass:** processos worker rodam como `ai_sdr_worker` role (NOSUPERUSER mas BYPASSRLS) ou setam `SET LOCAL row_security = off`. Justificativa: worker precisa cross-tenant pra carregar submissions/executions sem saber tenant_id antecipadamente.

## 4. Async-first

Todo I/O é async. Sem chamadas bloqueantes em handlers ou jobs.

- HTTP client: `httpx.AsyncClient` (já no projeto)
- DB: `sqlalchemy[asyncio]` + `asyncpg`
- Redis: `redis.asyncio`
- Sem `time.sleep()` — usar `asyncio.sleep()`

## 5. Idempotência por design

**Toda operação que escreve em sistema externo precisa ser idempotente.**

Padrões usados:

| Cenário | Estratégia |
|---|---|
| Webhook recebendo a mesma submission 2x | UNIQUE `(tenant_id, provider, external_id)` em `inbound_form_submissions` + ON CONFLICT DO NOTHING |
| `on_collected` disparado pelo mesmo turno 2x | UNIQUE `(talk_id, field, value_hash)` em `action_executions` (FE-03c já garante) |
| Worker retry após crash mid-execute | `Backend.create_or_update_contact` faz lookup local + remoto antes de criar |
| Migration rodada 2x | Alembic já gerencia |

**ABC `CRMBackend` documenta no docstring:** "Implementations MUST be idempotent. Worker may retry execute() after partial crashes."

## 6. Naming conventions

- **Módulos:** snake_case (`form_inbound`, `rdstation`, `crm_canonical`)
- **Classes:** PascalCase (`FormProviderAdapter`, `CRMActionAdapter`)
- **Funções/variáveis:** snake_case (`create_or_update_contact`, `field_values`)
- **Constantes:** UPPER_SNAKE (`FORM_PROVIDERS`, `CRM_BACKENDS`, `DEFAULT_TIMEOUT`)
- **Exceptions:** PascalCase + `Error` suffix (`SignatureError`, `MalformedPayload`, `AuthError`)

**Vocabulário específico desta spec:**

| Termo | Significado |
|---|---|
| `FormProviderAdapter` | ABC pra entrada de form |
| `IngestedFormSubmission` | Dataclass normalizada pós-parsing |
| `LeadIdentifier` | Pydantic pra resolver Lead (phone, email, label) |
| `CRMActionAdapter` | ActionAdapter genérico que despacha pro backend |
| `CRMBackend` | ABC por vendor (RDStation, HubSpot futuro) |
| `ContactCanonical` / `DealCanonical` | Pydantic do vocabulário interno PeSDR |
| `DealStage` | `Literal["open", "won", "lost"]` |

## 7. Error handling tipado

Exceções específicas por categoria, não `Exception` genérico.

**Form errors (`forms/errors.py`):**
- `SignatureError` → HTTP 401
- `MalformedPayload` → HTTP 400
- `FormProviderError` (base) — catch-all

**CRM errors (`flowengine/actions/crm/errors.py`):**
- `AuthError` — token inválido/refresh falhou → terminal failure
- `RemoteResourceGone` — 404 (entidade deletada externamente) → terminal + marca stale
- `ValidationError` — 422 (payload ruim) → terminal (não retry)
- `RateLimitError(retry_after_s)` — 429 → tenacity backoff
- `TransientError` — 5xx, network, timeout → tenacity backoff
- `UnknownHandlerError` — handler não suportado pelo backend → terminal

Pattern de classificação por status:

```python
def _classify_error(status: int, body: dict) -> Exception:
    if status in (401, 403): return AuthError(...)
    if status == 404: return RemoteResourceGone(...)
    if status == 422: return ValidationError(...)
    if status == 429: return RateLimitError(retry_after=int(body.get('retry_after', 30)))
    if status >= 500: return TransientError(...)
    if status >= 400: return ValidationError(...)
    return TransientError(...)  # network, timeout
```

## 8. Observability (structlog events)

**Eventos estruturados pra audit/debug:**

```python
log.info("form.submission.parsed", tenant=..., provider=..., field_count=...)
log.info("form.lead.created", tenant=..., lead_id=..., whatsapp_e164=...)
log.warning("form.submission.invalid_phone", tenant=..., raw_phone=...)
log.info("crm.rdstation.contact_created", lead_id=..., external_id=...)
log.warning("crm.rdstation.token_expired", retried=True)
log.error("crm.rdstation.refresh_failed", err=...)
log.info("action.crm.executed", execution_id=..., handler=..., external_id=...)
```

**Padrão:** `domain.subject.verb` em snake_case. Kwargs explícitos (não posicional).

## 9. Secrets via SOPS

Secrets ficam em `tenants/<slug>/secrets.enc.yaml` cifrados via SOPS + age (padrão estabelecido).

Esta spec adiciona 4 chaves:
- `respondi_webhook_secret` (string aleatória que Pedro gera)
- `rdstation_refresh_token` (obtido via OAuth flow inicial — ver `scripts/oauth_flow_init.py`)
- `rdstation_client_id` (app criado no painel RD Station Developers)
- `rdstation_client_secret` (idem)

`tenant.yaml` referencia via `<chave>_ref: secrets/<nome_chave>`. Loader resolve no startup.

## 10. Schema bumping

- **TreeFlow YAML:** bump semver `version: x.y.z` ao alterar conteúdo. Runtime recusa re-publicar mesma version com hash diferente (Plano 2 rule).
- **Migrations:** numeração incremental `0030_*`, `0031_*`. Alembic gera deterministic revision IDs.
- **Tenant.yaml schema:** evolução Pydantic é versionada via teste — quebrar shape antigo exige migration de tenant.yaml files manualmente.

## 11. Tests sob `tests/` (não em pasta do módulo)

```
tests/
├── unit/
│   ├── test_form_provider_base.py
│   ├── test_form_respondi_adapter.py
│   ├── test_crm_canonical_models.py
│   └── ...
├── integration/
│   ├── test_inbound_form_submissions_rls.py
│   ├── test_form_webhook_route.py
│   └── ...
└── fixtures/
    ├── respondi/
    │   ├── submission_text_form.json
    │   └── submission_with_utms.json
    └── rdstation/
        ├── create_contact_response.json
        └── create_deal_response.json
```

**Markers:**
- `@pytest.mark.unit` (implícito — qualquer teste em `unit/`)
- `@pytest.mark.integration` — precisa DB + Redis
- `@pytest.mark.live_llm` — hits API real (skip por default)
- `@pytest.mark.live_rdstation` (novo) — hits RD Station sandbox/prod (gated por `LIVE_RDSTATION=1`)

## 12. Pre-commit hooks (já configurados)

Antes de commitar, rodar:

```bash
make lint    # ruff check
make format  # ruff format
make type    # mypy src
make test-unit
```

Integration tests precisam `make up` (docker-compose).

## 13. Commits + PRs

- **1 commit por task.** Mensagem: `<type>(<scope>): <task_id> — <summary>` (e.g., `feat(forms): A5 — RespondiFormAdapter parser`)
- **Co-Authored-By:** Claude Opus 4.7 <noreply@anthropic.com> quando aplicável
- **PRs:** seguir tabela §8.4 da spec (3 PRs separados — Fase A, B, C)

## 14. Anti-patterns a evitar

❌ Engolir exceção sem logar
❌ Salvar token longo (refresh_token) em logs estruturados — só em SOPS
❌ Sync I/O em path async (bloqueia loop)
❌ Query sem `set_tenant_context()` em tabela RLS
❌ `dict[str, Any]` em borda pública (use Pydantic)
❌ Hardcode de URLs/endpoints (vir de config)
❌ Mock parcial de adapter em integration test — usar Fake explícito
