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

## Checkpointer notes

- Tabelas do LangGraph (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) são criadas pelo `ensure_checkpointer_schema()` no startup (chamado no lifespan da FastAPI e no `ai-sdr simulate`). Migration 0004 é só um stamp documental — NÃO cria as tabelas (a lib usa psycopg3, alembic env usa asyncpg).
- Tabelas do checkpointer NÃO têm `tenant_id` nem RLS. Isolamento via:
  1. `thread_id` sempre prefixado com `tenant_id:` (enforced por `TalkFlowRuntime.create`)
  2. RLS em `talkflows` (lookup `talkflow_id → thread_id`)
- Wipe pra dev fresh: `docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations;"`
