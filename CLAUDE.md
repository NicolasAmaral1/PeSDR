# PeSDR — Claude Code Instructions

## Project context

Multi-tenant AI SDR platform. Full design: `docs/superpowers/specs/2026-05-21-ai-sdr-design.md`.
Implementation plans: `docs/superpowers/plans/`.

Pilot tenant: mentora de marca pessoal (2 funis: Mentoria R$ 6.000 e Aceleradora R$ 1.497–2.000, + downsell R$ 247).

## Tech stack

Python 3.12 · uv · FastAPI · SQLAlchemy 2 (async, asyncpg) · Alembic · Postgres 16 + pgvector · Redis · structlog · LangGraph (próximos planos) · pytest · ruff · mypy · SOPS + age.

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
