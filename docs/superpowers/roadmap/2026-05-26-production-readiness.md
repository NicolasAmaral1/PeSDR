# PeSDR — Production Readiness & Otimização Arquitetural

**Data:** 2026-05-26
**Autor:** Pedro Aranda (audit técnico assistido por Claude Code, com lente do estado da arte 2026)
**Status:** Draft pra revisão e merge em `main`
**Branch:** `dev/pedro`
**Tipo:** Roadmap arquitetural — não é spec de feature nem plano de implementação. É a lista priorizada do que ainda precisa ser endereçado antes da primeira subida em produção e o que deve ser otimizado ao longo do crescimento do sistema.

**Audiência:** colaboradores do PeSDR (Pedro Aranda, Nicolas Amaral). Documento vivo — atualizar à medida que itens são fechados, novos riscos surgem, ou métricas-gatilho são atingidas.

---

## Sumário Executivo (TL;DR)

O PeSDR está, em 2026-05-26, na seguinte situação:

- **Planos 1–5 entregues** ([Foundation + Multi-tenancy](../plans/2026-05-21-foundation-multitenancy.md), [TreeFlow Engine + LangGraph](../plans/2026-05-22-treeflow-engine-langgraph.md), [KB + Guardrails](../plans/2026-05-23-kb-and-guardrails.md), [Objection Classifier 4a](../plans/2026-05-25-plan4a-objection-classifier.md), [Messaging Adapter + WhatsApp Cloud](../plans/2026-05-25-messaging-adapter-whatsapp.md)) e mergeados em `main` no commit `99703c0`.
- **Stack técnico alinhado com práticas 2026** — Python 3.12 + FastAPI + SQLAlchemy 2 async + Postgres 16 + pgvector + LangGraph + adapter pattern. Decisões maduras (TDD, RLS, prompt caching desde o dia 1, multi-provider via `init_chat_model`, idempotency em 3 camadas).
- **Dívidas identificadas** se separam em **dois grupos**:
  1. **10 itens urgentes** (bloqueiam ou tornam frágil a 1ª produção) — agrupados em 3 ondas sequenciais (Onda 0, Onda 1, Onda 2)
  2. **18 otimizações** (não bloqueiam, mas se ignoradas viram tech debt caro) — agrupadas por categoria de impacto e ativadas por gatilho de escala

**Antes de ligar o primeiro cliente real** (Joana Mentora), as 3 ondas urgentes devem estar fechadas. Estimativa total: **~3 semanas** com 2 devs trabalhando em paralelo.

---

## Índice

1. [Contexto e princípios](#1-contexto-e-princípios-do-roadmap)
2. [🔴 PARTE 1 — Urgente (pré-produção)](#-parte-1--urgente-pré-produção)
   - [Onda 0 — Foundation Infra](#onda-0--foundation-infra-semana-1)
   - [Onda 1 — Safety & Observability](#onda-1--safety--observability-semana-2)
   - [Onda 2 — Compliance & Readiness](#onda-2--compliance--readiness-semana-3)
   - [Critérios "pronto pra produção"](#critérios-pronto-pra-produção)
3. [🟡 PARTE 2 — Otimizações (escala saudável)](#-parte-2--otimizações-escala-saudável)
   - [A · Performance & Scale](#a--performance--scale)
   - [B · Observabilidade Avançada](#b--observabilidade-avançada)
   - [C · Maturidade Multi-Tenant](#c--maturidade-multi-tenant)
   - [D · Feature Breadth (próximos planos)](#d--feature-breadth-próximos-planos)
   - [E · Prevenção de Tech Debt](#e--prevenção-de-tech-debt)
   - [F · Compliance & Governança](#f--compliance--governança)
4. [Anexos](#anexos)
   - [A · Tabela de versões a pinar/upgradar](#anexo-a--versões-a-pinarupgradar)
   - [B · Métricas-gatilho](#anexo-b--métricas-gatilho)
   - [C · Glossário](#anexo-c--glossário)
   - [D · Decisões pendentes](#anexo-d--decisões-pendentes-pedro--nicolas)

---

## 1. Contexto e princípios do roadmap

### 1.1 O que já está pronto (recapitulação)

| # | Plano | Entrega principal | Arquivos-chave |
|---|---|---|---|
| 1 | Foundation + Multi-tenancy | uv/Docker/Postgres+pgvector/Redis · FastAPI · Alembic · RLS via `set_tenant_context` · SOPS+age secrets · structlog JSON · `/health` | [src/ai_sdr/db/rls.py](../../../src/ai_sdr/db/rls.py), [src/ai_sdr/settings.py](../../../src/ai_sdr/settings.py), [docker-compose.yml](../../../docker-compose.yml), migrations `0001-0002` |
| 2 | TreeFlow Engine + LangGraph | YAML→`StateGraph` compiler · `TalkFlowRuntime.publish_version/create/step` · checkpointer Postgres · `simpleeval` p/ expressions · CLI `simulate` | [src/ai_sdr/treeflow/compiler.py](../../../src/ai_sdr/treeflow/compiler.py), [src/ai_sdr/treeflow/runtime.py](../../../src/ai_sdr/treeflow/runtime.py), [src/ai_sdr/treeflow/state.py](../../../src/ai_sdr/treeflow/state.py), migrations `0003-0004` |
| 3 | KB + Guardrails | Markdown chunker (`cl100k_base`) · OpenAI embeddings (1536d) · pgvector IVFFlat retriever · whitelist + critic (Haiku) com retry/fallback · prompt caching Anthropic · multi-provider LLM via `init_chat_model` | [src/ai_sdr/kb/](../../../src/ai_sdr/kb/), [src/ai_sdr/guardrails/](../../../src/ai_sdr/guardrails/), [src/ai_sdr/llm/factory.py](../../../src/ai_sdr/llm/factory.py), migration `0005` |
| 4a | Objection Classifier | Haiku classifier antes do main LLM · `handles_objections` (Node) + `global_objections` (TreeFlow) · resposta inline OU sub-node via `as_subnode` · sentinel `BACK_TO_ORIGIN` · `ObjectionRecord` em state | [src/ai_sdr/treeflow/classifier.py](../../../src/ai_sdr/treeflow/classifier.py), [src/ai_sdr/treeflow/objection_response.py](../../../src/ai_sdr/treeflow/objection_response.py) |
| 5 | Messaging Adapter + WhatsApp | `MessagingAdapter` ABC · `WhatsAppCloudAPIAdapter` (HMAC, retry, taxonomia de erros) · webhook `/webhooks/{tenant}/{provider}` · worker arq c/ `pg_advisory_lock` per-lead · modelo `Lead` (`pending_assignment→active→unreachable`) · CLI/REST `assign-lead` | [src/ai_sdr/messaging/](../../../src/ai_sdr/messaging/), [src/ai_sdr/worker/](../../../src/ai_sdr/worker/), [src/ai_sdr/api/routes/](../../../src/ai_sdr/api/routes/), migrations `0006-0008` |

**ADR transversal:** [Standalone-first + Vialum-optional via adapter pattern](../specs/2026-05-24-adapter-pattern-decision.md) (2026-05-24).

### 1.2 Filosofia de classificação urgente vs otimização

Três critérios determinam se um item é urgente:

1. **Blast radius em produção** — se quebrar, vaza dados, queima dinheiro, ou interrompe operação?
2. **Bloqueio de dependências** — trava outros planos ou trabalho de outros devs?
3. **Custo de reverter** — se descoberto depois de produção rodar, é refactor caro ou intrusivo?

**URGENTE** = item com SIM em **≥ 2** dos 3 critérios.
**OTIMIZAÇÃO** = item com 0–1 SIM, ou cujo gatilho de necessidade está bem definido no futuro (métrica observável).

### 1.3 Conceitos usados neste documento

- **Onda** — lote sequencial de mudanças. Onda N+1 só começa quando a Onda N estabilizou em `main`.
- **Gatilho** — métrica observável ou condição que sinaliza "agora é a hora desta otimização".
- **Owner sugerido** — heurística (não vinculante). Considera quem tem mais contexto na área. Pode ser ajustado em conversa.
- **Effort** — estimativa de tempo de implementação por 1 dev focado, sem incluir review/QA. Faixa: `S` (≤ 1 dia), `M` (2–4 dias), `L` (1–2 semanas), `XL` (3+ semanas).
- **"Done when"** — critério objetivo de conclusão (não "estou cansado", mas "X testa passou e Y métrica é verificável").

### 1.4 Limites deste roadmap

- **Não substitui as specs/plans** existentes em [docs/superpowers/specs/](../specs/) e [docs/superpowers/plans/](../plans/). É um overlay priorizando o que falta e o que pode esperar.
- **Não cobre escopo de produto** (que features adicionar, que persona atender, qual modelo de venda). Foco é técnico-arquitetural.
- **Datas são estimativas, não compromissos** — cada item tem dependências que podem reescalonar.
- **Não substitui code review** — todo item urgente fechará via PR com revisão do outro dev.

---

## 🔴 PARTE 1 — Urgente (pré-produção)

> **Princípio:** nenhum cliente real (Joana) deve entrar em produção com qualquer um destes 10 itens em aberto. Não há atalhos — saltar um item urgente custa muito mais a posteriori do que parece.

### Onda 0 — Foundation Infra (semana 1)

> **Por que essa onda existe antes de tudo:** estabelece os trilhos onde as outras mudanças vão rodar. Sem CI/CD não temos confiança de deploy; sem versões pinadas não temos previsibilidade; sem backup não temos sobrevivência a incidentes.

---

#### 0.1 — Pin de versões em libs young (langgraph, langchain, langchain-*)

**O quê:** Em [pyproject.toml](../../../pyproject.toml), substituir `>=` por `==` em todas as libs do ecossistema LangChain/LangGraph, que estão em fase de evolução rápida (pré-1.0 em alguns casos).

**Por quê:**
LangGraph está em `0.2.x` (pré-1.0 estável). LangChain saltou pra `1.x` em out/2025 com breaking changes silenciosos em alguns sub-pacotes (`langchain-anthropic` mudou shape de `SystemMessage` com `cache_control`, `langchain-openai` mudou semântica de `with_structured_output`). Hoje [pyproject.toml](../../../pyproject.toml) tem `langgraph>=0.2.60` — qualquer `uv sync` num dev novo pode trazer 0.3.x e quebrar [src/ai_sdr/treeflow/compiler.py](../../../src/ai_sdr/treeflow/compiler.py:42-44) (que usa `Command`, `add_conditional_edges`, sentinels específicos).

Em prod, uma upgrade silenciosa de uma sub-lib pode quebrar a conversa de um cliente real. **Determinismo de versão é prerequisito de operação confiável.**

**Como (diff aplicado):**

```diff
-    "langgraph>=0.2.60",
-    "langgraph-checkpoint-postgres>=2.0.21",
-    "langchain-core>=0.3.28",
-    "langchain>=1.3.0",
-    "langchain-anthropic>=0.3.0",
-    "langchain-openai>=0.2.14",
-    "langchain-google-genai>=2.0.0",
-    "langchain-deepseek>=0.1.0",
-    "langchain-ollama>=0.2.0",
+    "langgraph==0.2.60",
+    "langgraph-checkpoint-postgres==2.0.21",
+    "langchain-core==0.3.28",
+    "langchain==1.3.0",
+    "langchain-anthropic==0.3.0",
+    "langchain-openai==0.2.14",
+    "langchain-google-genai==2.0.0",
+    "langchain-deepseek==0.1.0",
+    "langchain-ollama==0.2.0",
```

E para libs **estáveis** (fastapi, sqlalchemy, pydantic, etc.), manter `>=` é OK — não envelheceram mal historicamente.

**Effort:** `S` (~30min)
**Owner:** qualquer dev
**Dependências:** nenhuma
**Risk se não feito:** dev novo faz `uv sync` 3 meses depois → comportamento muda silenciosamente em produção
**Done when:**
- [ ] `uv.lock` regenerado (`uv lock --upgrade`) e versões batem com as pinadas
- [ ] `make test-unit && make test-integration` passa
- [ ] PR mergeado em `main`

**Nota operacional:** a upgrade dessas libs vira decisão consciente (issue dedicada + PR). Procedure: bumpar 1 lib por vez, rodar full test suite, ver se algum live_llm test quebrou.

---

#### 0.2 — CI/CD básico via GitHub Actions

**O quê:** Criar 2 workflows em `.github/workflows/`:
- `ci.yml` — em todo PR pra `main`: lint, type-check, test (unit + integration com containers).
- `deploy.yml` — em push pra `main`: build da imagem Docker, push pra `ghcr.io/nicolasamaral1/pesdr:latest`, SSH na VPS, `docker compose pull && up -d`.

**Por quê:**
Hoje deploy é **SSH manual** ([README §VPS](../../../README.md)). Sem CI:
- Bug crítico em prod → janela de 15+ minutos entre fix e deploy
- Sem gate de qualidade entre merge e prod (alguém pode mergear PR vermelho)
- Onboarding de novo dev demora (precisa decorar comandos)
- Sem audit trail de quem deployou quando

Em 2026, **CI/CD é higiene básica** — não é "feature", é commodity.

**Como (estrutura):**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint-and-type:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.x"
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: ai_sdr
          POSTGRES_PASSWORD: ai_sdr_dev
          POSTGRES_DB: ai_sdr
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U ai_sdr"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    env:
      DATABASE_URL: postgresql+asyncpg://ai_sdr:ai_sdr_dev@localhost:5432/ai_sdr
      REDIS_URL: redis://localhost:6379/0
      APP_ENV: test
      LOG_LEVEL: WARNING
      TENANTS_DIR: tenants
      SOPS_AGE_KEY_FILE: /tmp/age.key
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: |
          # Apply db-init scripts manually (sin docker-compose)
          PGPASSWORD=ai_sdr_dev psql -h localhost -U ai_sdr -d ai_sdr -f db-init/00-extensions.sql
          PGPASSWORD=ai_sdr_dev psql -h localhost -U ai_sdr -d ai_sdr -f db-init/01-create-app-role.sql
      - run: uv run alembic upgrade head
      - run: uv run pytest tests/unit -v
      - run: uv run pytest tests/integration -v -m "not live_llm"
```

`.github/workflows/deploy.yml` (esboço — afina conforme registro escolhido):

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    if: github.event.head_commit.author.email != 'github-actions@github.com'
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          push: true
          tags: |
            ghcr.io/nicolasamaral1/pesdr:latest
            ghcr.io/nicolasamaral1/pesdr:${{ github.sha }}
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: root
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /root/PeSDR
            git pull origin main
            docker compose pull
            docker compose up -d
            uv run alembic upgrade head
```

**Secrets a configurar no GitHub:** `VPS_HOST`, `VPS_SSH_KEY` (chave privada SSH com acesso à VPS).

**Effort:** `M` (1–2 dias — incluindo afinar CI, configurar registry, debugar testes em ambiente CI vs local)
**Owner:** Pedro (boa primeira contribuição autônoma — não toca código de produto)
**Dependências:** 0.1 (versões pinadas pra `uv sync --frozen` ser estável no CI)
**Risk se não feito:** primeira regressão em prod = 30+ min sem operação
**Done when:**
- [ ] PR de teste com bug intencional (e.g. `assert False` em algum test) é rejeitado pelo CI
- [ ] Push em `main` triggera deploy e VPS reflete a mudança em < 10min
- [ ] Documentação de "como adicionar VPS_SSH_KEY" em [CLAUDE.md](../../../CLAUDE.md) ou novo `docs/ops/deploy.md`

---

#### 0.3 — Backup automatizado + restore testado

**O quê:** Configurar pg_dump diário automatizado da Postgres na VPS, com upload pra storage S3-compatible externo (Backblaze B2 ou Hetzner Object Storage — ambos baratos e geograficamente diversos da Hostinger).

**Por quê:**
Hoje **não existe backup formalizado** do Postgres de produção. Se o disco da VPS corromper, der bug em alguma migration destrutiva, ou tenant for atacado, **perde-se TUDO** (TreeFlows, talkflows, mensagens, KB chunks indexados). Pra cliente piloto pode parecer aceitável; pra um cliente pagante já não é.

Princípio operacional: **backup que nunca foi restaurado é só uma ilusão**. Tem que testar o restore pelo menos 1 vez em ambiente staging.

**Como (esboço):**

Cron diário na VPS (`/etc/cron.d/pesdr-backup`):

```bash
# Daily backup às 03:00 local
0 3 * * * root /root/PeSDR/scripts/backup.sh >> /var/log/pesdr-backup.log 2>&1
```

`scripts/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="/tmp/pesdr-${TIMESTAMP}.sql.gz"

# Dump from running container
docker exec ai_sdr_postgres pg_dump \
  -U ai_sdr \
  -d ai_sdr \
  --no-owner \
  --no-acl \
  --format=plain \
  | gzip > "${BACKUP_FILE}"

# Upload to Backblaze B2 (or Hetzner)
b2 upload-file pesdr-backups "${BACKUP_FILE}" "postgres/${TIMESTAMP}.sql.gz"

# Retain locally 7 days, remote 90 days (Backblaze rule)
find /tmp -name "pesdr-*.sql.gz" -mtime +7 -delete

# Notify on failure (basic)
echo "Backup completed: ${TIMESTAMP}"
```

**Restore drill** (executar 1x por mês em ambiente staging):

```bash
# Em VPS staging
docker compose down -v   # apaga volume
docker compose up -d postgres

# Aguarda healthy
docker exec ai_sdr_postgres pg_isready -U ai_sdr

# Restore do dump mais recente
gunzip -c /tmp/pesdr-latest.sql.gz | docker exec -i ai_sdr_postgres psql -U ai_sdr -d ai_sdr

# Reaplicar migrations alembic (idempotente)
uv run alembic upgrade head

# Smoke test
curl http://localhost:8200/health
```

**Effort:** `S` (4–6h — script + cron + 1 restore drill)
**Owner:** Pedro ou Nicolas (qualquer um — é ops básico)
**Dependências:** nenhuma
**Risk se não feito:** disk failure ou bug de migration destrutiva = perda total de dados
**Done when:**
- [ ] Cron rodando há 3+ dias e logs limpos
- [ ] Backup mais recente listado no B2/Hetzner via CLI
- [ ] 1 restore drill executado com sucesso (registrar em `docs/ops/restore-drills.md`)
- [ ] Documentação de "como restaurar" em runbook (item 2.3)

**Decisão técnica diferida:** PITR via WAL archiving (pgBackRest/Barman) é mais robusto que dump diário, mas adiciona complexidade e custo. Para 1ª produção, dump diário é suficiente. Migrar pra WAL archiving entra na otimização [A.4](#a4--postgres-replicas-pra-read-scaling).

---

### Onda 1 — Safety & Observability (semana 2)

> **Por que essa onda vem depois da 0:** com CI/backup em pé, agora podemos mexer no caminho crítico (LLM, cost, observability) com segurança. Cada item aqui evita um cenário catastrófico real: cost runaway, debugging cego em prod, abuse/DDoS, MITM.

---

#### 1.1 — Cost ceiling enforcement (LimitsConfig + Redis counter + circuit breaker)

**O quê:** Implementar enforcement em código do `tenant.limits.max_usd_per_day` que existe no spec [§6.1](../specs/2026-05-21-ai-sdr-design.md) mas **não está no código** atualmente.

**Por quê:**
Hoje qualquer tenant pode gastar quantia ilimitada em LLM:
- Loop bug no classifier que detecta objection toda turn → 2× LLM calls eternamente
- Tenant atacado (mensagens infinitas) → custo escala linearmente com inbound
- Modelo configurado errado (alguém troca pra Opus em [tenants/example/tenant.yaml](../../../tenants/example/tenant.yaml) sem perceber preço) → custo 5×

Em 2024-2025, **várias startups quebraram** por runaway LLM costs (referência: posts do Vercel/Lambda sobre vazamento de OpenAI key, etc.). PeSDR está exposto a isso hoje.

A defesa não precisa ser perfeita — precisa **interromper antes que o estrago seja grande**. Uma cota diária bloqueada em redis counter já resolve 95% dos cenários.

**Como (esboço):**

1. **Schema** ([src/ai_sdr/schemas/tenant_yaml.py](../../../src/ai_sdr/schemas/tenant_yaml.py)) — adicionar:

   ```python
   class LimitsConfig(BaseModel):
       model_config = ConfigDict(extra="forbid")
       max_usd_per_day: float = Field(default=50.0, ge=0.01, le=10_000.0)
       alert_at_pct: int = Field(default=80, ge=1, le=99)
       hard_block_at_pct: int = Field(default=100, ge=1, le=200)
       fallback_text_over_budget: str = Field(
           default="Estamos com instabilidade momentânea, te respondo em instantes.",
           min_length=10,
       )

   class TenantConfig(BaseModel):
       # ... existente ...
       limits: LimitsConfig = Field(default_factory=LimitsConfig)
   ```

2. **Cost tracker** (novo módulo `src/ai_sdr/observability/cost_tracker.py`):

   ```python
   class CostTracker:
       def __init__(self, redis: Redis, limits: LimitsConfig, tenant_id: UUID) -> None: ...

       async def can_proceed(self) -> tuple[bool, float]:
           """Returns (allowed, current_pct). Reads atomically from Redis."""

       async def record(self, usd_cost: float) -> None:
           """Increment counter (key: cost:tenant:<id>:<YYYY-MM-DD>, TTL 25h)."""

       async def emit_alert_if_threshold_crossed(self, before: float, after: float) -> None:
           """Emit structlog event if crossed alert_at_pct (idempotent)."""
   ```

3. **Wire em [src/ai_sdr/treeflow/compiler.py](../../../src/ai_sdr/treeflow/compiler.py)** dentro de `_make_node_fn` — antes do `extract()`:

   ```python
   tracker = CostTracker(redis, tenant_limits, tenant_id)
   allowed, pct = await tracker.can_proceed()
   if not allowed:
       logger.warning("cost.limit.hard_block", tenant_id=str(tenant_id), pct=pct)
       return {
           "response_text": tenant_limits.fallback_text_over_budget,
           "collected": {},
           "messages": [...],
           # don't advance node — let operator unblock
       }
   # ... extract LLM call ...
   await tracker.record(estimated_cost_usd)
   ```

4. **Cost estimation** — token count via `tiktoken` (já no projeto), preço hardcoded por modelo num dict (`PRICING = {"claude-sonnet-4-6": {"input": 3e-6, "output": 15e-6}, ...}`).

5. **Job de reset diário** (arq cron) — limpa contadores antigos, emite report do dia anterior.

**Effort:** `M` (2–3 dias)
**Owner:** Nicolas (mexe no compiler — área dele) — ou Pedro coordenando review fechada
**Dependências:** nenhuma direta; afinará com [1.2 LangFuse](#12--langfuse-integration-llm-observability) que pode substituir parte da estimação manual
**Risk se não feito:** 1 tenant runaway pode queimar R$ 1k+ antes que alguém perceba
**Done when:**
- [ ] `tenant.yaml` valida `limits:` block (test unit)
- [ ] Test integration: mock 100 LLM calls com cost = limit/100; 101ª retorna fallback
- [ ] Test integration: alert event emitido aos 80% (não aos 79%, não aos 81%) — idempotência verificada
- [ ] Counter em Redis sobrevive a restart da API/worker (não é in-memory)
- [ ] [CLAUDE.md](../../../CLAUDE.md) ganha seção "Cost controls"

**Decisão técnica diferida:** Estimar cost real (pegando `usage` do response) vs. cost predito (token count × pricing) — escolhemos predito por simplicidade no MVP. Quando [1.2 LangFuse](#12--langfuse-integration-llm-observability) entrar, real cost vem de graça.

---

#### 1.2 — LangFuse integration (LLM observability)

**O quê:** Integrar [LangFuse](https://langfuse.com/) (open-source, self-host) via callback do LangChain, capturando trace de toda LLM call: prompt input, output, tokens, latency, cost, model, tenant.

**Por quê:**
Hoje observability de LLM no PeSDR é structlog JSON apenas. Isso não permite:
- **Prompt drift**: qual versão do prompt foi rodada quando? Conseguir reproduzir uma conversa específica.
- **Quality regression**: trocou de modelo no tenant.yaml — saída piorou? Onde?
- **Cost analytics**: top tenants por custo, top nodes por custo, custo médio por turn.
- **Trace de cadeia**: webhook → classifier (Haiku) → KB retrieve → main LLM (Sonnet) → critic (Haiku) → adapter.send. Em prod debugar isso via stdout é inviável.
- **Eval/golden datasets**: marcar conversas exemplares pra usar como benchmark de regressão.

Em 2026, **operar LLM em prod sem trace tool é considerado amadorismo**. As 3 opções dominantes:

| Tool | Onde roda | Custo | Quando faz sentido |
|---|---|---|---|
| **LangFuse** | Self-host (Docker) ou cloud | $0 self-host | **Melhor fit pro PeSDR** — open source, sem vendor lock, roda na VPS já existente, integra direto com LangChain |
| **Helicone** | SaaS proxy ou async | $50–500/mês conforme volume | Mais fácil setup, mas vendor lock e custos sobem |
| **LangSmith** | SaaS LangChain oficial | $39/dev/mês minimum | Caro pra prod; melhor pra dev/eval |

Recomendação: **LangFuse self-hosted** num container ao lado dos outros na docker-compose.

**Como:**

1. Subir LangFuse na VPS (docker-compose):

   ```yaml
   # docker-compose.yml — adicionar
   langfuse-db:
     image: postgres:16
     environment:
       POSTGRES_USER: langfuse
       POSTGRES_PASSWORD: ${LANGFUSE_DB_PASSWORD}
       POSTGRES_DB: langfuse
     volumes: [langfuse_db_data:/var/lib/postgresql/data]
   langfuse:
     image: langfuse/langfuse:latest
     depends_on: { langfuse-db: { condition: service_healthy } }
     environment:
       DATABASE_URL: postgresql://langfuse:${LANGFUSE_DB_PASSWORD}@langfuse-db:5432/langfuse
       NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
       SALT: ${LANGFUSE_SALT}
       NEXTAUTH_URL: https://langfuse.luminai.ia.br
     ports: ["3000:3000"]
   ```

2. Lib em [pyproject.toml](../../../pyproject.toml): `langfuse>=2.50` (versão estável em 2026).

3. Wire em [src/ai_sdr/main.py](../../../src/ai_sdr/main.py) (lifespan):

   ```python
   from langfuse.callback import CallbackHandler

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       # ... existente ...
       app.state.langfuse_handler = CallbackHandler(
           public_key=settings.langfuse_public_key,
           secret_key=settings.langfuse_secret_key,
           host=settings.langfuse_host,
       )
   ```

4. Passar `callbacks=[handler]` em `init_chat_model` no [src/ai_sdr/llm/factory.py](../../../src/ai_sdr/llm/factory.py).

5. Adicionar `session_id` e `user_id` aos calls (mapeia pro `talkflow_id` e `lead_id`) — vira facetagem no dashboard.

**Effort:** `M` (2–3 dias — incluindo subir LangFuse, configurar Traefik, validar trace end-to-end)
**Owner:** Pedro (boa exposição arquitetural sem mexer no core)
**Dependências:** 0.2 (CI/CD pra deployar a nova service)
**Risk se não feito:** debugar conversas em prod é "spelunking" — operação não escala
**Done when:**
- [ ] LangFuse acessível via `https://langfuse.luminai.ia.br` (Traefik route + cert)
- [ ] 1 turn de [`ai-sdr simulate`](../../../src/ai_sdr/cli/simulate.py) aparece como trace completo no dashboard (classifier + main + critic visíveis como spans)
- [ ] Cost por trace bate (±5%) com [1.1 cost tracker](#11--cost-ceiling-enforcement-limitsconfig--redis-counter--circuit-breaker) — sinal de que estimativas estão calibradas
- [ ] Dashboard com 3 views salvas: "Cost por tenant (7d)", "Latency P95 por modelo", "Top 10 conversations por custo"

**Decisão técnica diferida:** mover do estimador manual de cost ([1.1](#11--cost-ceiling-enforcement-limitsconfig--redis-counter--circuit-breaker)) pra ler `langfuse.observation.usage` direto — fazer depois de 1 semana de validação.

---

#### 1.3 — Rate limiting nos webhooks

**O quê:** Adicionar rate limiting em `POST /webhooks/{tenant_slug}/{provider}` ([src/ai_sdr/api/routes/webhooks.py](../../../src/ai_sdr/api/routes/webhooks.py)) e em `POST /tenants/{slug}/leads/{id}/assign` ([src/ai_sdr/api/routes/leads.py](../../../src/ai_sdr/api/routes/leads.py)).

**Por quê:**
Esses endpoints serão **públicos** (Meta WhatsApp Cloud só funciona se o webhook for acessível pela internet). Hoje **não há nenhum rate limiter** — qualquer um na internet pode:
- Bombardear o endpoint até esgotar recursos (DDoS trivial)
- Tentar HMAC bypass por força bruta (timing analysis seria mitigada por constant-time compare — ver [2.4](#24--security-review-final-hmac-timing-attack-secrets-rotation-strategy))
- Inundar `inbound_messages` com payloads falsos antes da verificação de signature (no caso o INSERT só roda APÓS verify ok, então minor risk — mas custo de CPU verificando HMAC × N requests é real)

Padrão da indústria em 2026: rate limit por IP + por tenant slug, com burst allowance pequena. Meta WhatsApp envia spike no momento da entrega de mensagens — não dá pra ser draconiano demais.

**Como (opção A — `slowapi` no FastAPI):**

```python
# pyproject.toml
"slowapi>=0.1.9",

# main.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# webhooks.py
@router.post("/webhooks/{tenant_slug}/{provider}")
@limiter.limit("100/minute")  # razoável pra WhatsApp Cloud spikes
async def webhook_ingest(...): ...
```

**Como (opção B — Traefik middleware):** rate limit no proxy reverso antes mesmo de chegar no FastAPI. Mais robusto (não consome CPU de Python) mas exige Traefik configurado (ver [1.4](#14--https-via-traefik--domínio-sdrluminaiibr)).

Recomendação: **B se Traefik já entrar nessa onda; A se quisermos blindar API isoladamente primeiro**.

**Effort:** `S` (4–6h)
**Owner:** Pedro
**Dependências:** se opção B, requer 1.4
**Risk se não feito:** primeiro DDoS amador derruba o serviço
**Done when:**
- [ ] Carga test (e.g. `hey -n 1000 -c 50 ...`) mostra 429 retornado após threshold
- [ ] Test integration: 101ª request do mesmo IP em 1min retorna 429
- [ ] Endpoint não trava o tenant inteiro (lead legítimo do mesmo tenant em IP diferente continua respondendo)

---

#### 1.4 — HTTPS via Traefik + domínio `sdr.luminai.ia.br`

**O quê:** Configurar Traefik como reverse proxy na frente do FastAPI, com cert automático via Let's Encrypt, em `sdr.luminai.ia.br`.

**Por quê:**
[README §VPS](../../../README.md) menciona que a API ficará "futuramente atrás de Traefik em `sdr.luminai.ia.br`" — esse "futuro" precisa ser agora. WhatsApp Cloud API **só aceita webhook em HTTPS válido**. Sem isso, **não há produção possível**.

Adicional: Traefik vira o ponto de aplicar rate limiting (1.3 opção B), headers de segurança (HSTS, CSP), e routing pra outros containers (LangFuse de 1.2, futuro dashboard Grafana de B.2).

**Como (esboço):**

`docker-compose.yml` — adicionar service:

```yaml
traefik:
  image: traefik:v3.2
  command:
    - "--providers.docker=true"
    - "--providers.docker.exposedbydefault=false"
    - "--entrypoints.web.address=:80"
    - "--entrypoints.websecure.address=:443"
    - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
    - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
    - "--certificatesresolvers.le.acme.email=ops@luminai.ia.br"
    - "--certificatesresolvers.le.acme.storage=/letsencrypt/acme.json"
    - "--certificatesresolvers.le.acme.tlschallenge=true"
  ports: ["80:80", "443:443"]
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
    - traefik_letsencrypt:/letsencrypt

api:
  # ... existente ...
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.pesdr.rule=Host(`sdr.luminai.ia.br`)"
    - "traefik.http.routers.pesdr.entrypoints=websecure"
    - "traefik.http.routers.pesdr.tls.certresolver=le"
    - "traefik.http.services.pesdr.loadbalancer.server.port=8000"
```

**DNS:** apontar `sdr.luminai.ia.br` A → IP da VPS (já existe `luminai.ia.br` no domínio — só adicionar subdomain).

**Effort:** `S` (4h se DNS já está em ordem)
**Owner:** Pedro ou Nicolas (qualquer um)
**Dependências:** nenhuma técnica; DNS access necessário
**Risk se não feito:** WhatsApp não aceita webhook = produção impossível
**Done when:**
- [ ] `curl -I https://sdr.luminai.ia.br/health` retorna 200 + headers válidos
- [ ] Cert renovado automaticamente (verificar log Traefik depois de 1 semana)
- [ ] HTTP redireciona pra HTTPS
- [ ] Webhook URL `https://sdr.luminai.ia.br/webhooks/example/whatsapp_cloud` configurável no Meta Business Manager

---

### Onda 2 — Compliance & Readiness (semana 3)

> **Por que essa onda fecha o ciclo:** com infra, safety e observability resolvidas, agora endurecemos pra prod. LGPD não é opcional no Brasil; validação multi-provider é seguro arquitetural; runbook + security review são higiene final.

---

#### 2.1 — LGPD baseline (disclaimer + retention + delete endpoint)

**O quê:** Implementar os 3 mínimos exigidos pela LGPD pra operar legalmente no Brasil:

1. **Disclaimer "você está conversando com IA"** na 1ª interação do lead (mensagem opening ou hint no Node `saudacao`)
2. **Política de retenção** configurável por tenant (default: 12 meses de inactividade → soft-delete)
3. **Endpoint de "esquecimento"**: `DELETE /tenants/{slug}/leads/{lead_id}` (operacional via CLI + REST)

**Por quê:**
O spec [§15](../specs/2026-05-21-ai-sdr-design.md) explicitamente coloca LGPD **fora do MVP** ("decisão do usuário"). **Essa decisão é incorreta pra primeira produção em território brasileiro.** ANPD pode aplicar multas (Art. 52 da LGPD: até 2% do faturamento, máx R$ 50M por infração).

Não estamos falando de SOC 2 nem ISO 27001 (esses são pós-MVP). Estamos falando do **mínimo legal** pra operar:
- **Art. 9** — Consentimento informado (lead saber que é IA)
- **Art. 16** — Eliminação de dados após uso ou pedido do titular
- **Art. 18 VI** — Direito à eliminação (DELETE endpoint)

A boa notícia: o esforço é **baixo** porque a arquitetura já está pronta (RLS facilita delete cascade, modelo `Lead` tem `status`).

**Como (esboço):**

1. **Disclaimer** — no node `saudacao` do TreeFlow:

   ```yaml
   # tenants/example/treeflows/example.yaml
   nodes:
     - id: saudacao
       prompt: |
         Você é uma SDR virtual em treinamento da Joana Mentora.
         ⚠️ IMPORTANTE: nas suas primeiras mensagens (turno 1 ou 2), mencione naturalmente
         que é uma assistente de IA. Ex: "Oi! Sou a assistente virtual da Joana, prazer te
         conhecer".
   ```

   Adicional opcional: header em todas as respostas (tag invisível pro WhatsApp, mas visível em audit).

2. **Retention** — adicionar a `tenant.yaml`:

   ```python
   class RetentionConfig(BaseModel):
       inactive_lead_months: int = Field(default=12, ge=1, le=120)
       soft_delete_on_request: bool = True  # vs hard delete
   ```

3. **Endpoint de delete** (novo route em [src/ai_sdr/api/routes/leads.py](../../../src/ai_sdr/api/routes/leads.py)):

   ```python
   @router.delete("/tenants/{tenant_slug}/leads/{lead_id}")
   async def delete_lead_data(
       tenant_slug: str,
       lead_id: UUID,
       reason: Literal["lgpd_request", "tenant_cleanup", "test"] = "lgpd_request",
   ):
       """LGPD Art. 18 VI — direito à eliminação.

       Soft delete:
         - lead.status = 'deleted'
         - lead.deleted_at = now()
         - Talkflows cascade-soft-delete (via FK)
         - Inbound_messages cascade-soft-delete
       Hard delete (após 30d soft):
         - Job worker periódico apaga rows com deleted_at < now() - 30d
       Audit:
         - Insert em deletion_log (quem, quando, motivo)
       """
   ```

4. **Migration nova** — adicionar `deleted_at TIMESTAMPTZ` em `leads`, `talkflows`, `inbound_messages`. Atualizar RLS pra `... AND deleted_at IS NULL`.

5. **CLI** — `ai-sdr leads delete --tenant <slug> --lead <uuid> --reason lgpd_request`.

6. **Job arq** — `cron: "0 4 * * *"` — purga hard delete após 30d soft.

**Effort:** `M` (3 dias — incluindo migration + tests RLS + CLI + auditing log)
**Owner:** Pedro (compliance é boa área pra contribuir sem mexer no core de LLM)
**Dependências:** 0.3 (backup — antes de mexer em delete, garantir que backup tá quente)
**Risk se não feito:** multa ANPD + reputational damage com Joana
**Done when:**
- [ ] Test integration: DELETE endpoint soft-deletes lead + cascade
- [ ] Test integration: lead soft-deleted some das queries normais (RLS funciona com `deleted_at IS NULL`)
- [ ] CLI funciona end-to-end
- [ ] Job cron de hard delete passa em integration test (simula `deleted_at < 30d ago`)
- [ ] [CLAUDE.md](../../../CLAUDE.md) ganha seção "LGPD baseline"
- [ ] Disclaimer testado em [`ai-sdr simulate`](../../../src/ai_sdr/cli/simulate.py) — primeira mensagem do agente menciona "IA"

**Não está em escopo nesta onda** (vai pra otimização [F.1](#f1--lgpd-complete-dpia-encryption-at-rest)):
- Encryption at rest do Postgres (LUKS/dm-crypt na VPS)
- DPIA (Data Protection Impact Assessment) formal
- Anonymização de logs antigos
- Termo de uso / política de privacidade publicada no site da Joana

---

#### 2.2 — Plano 4b: validation matrix multi-provider

**O quê:** Implementar o que o [ADR](../specs/2026-05-24-adapter-pattern-decision.md) menciona como pendente — validar end-to-end que cada provider LLM declarado em [pyproject.toml](../../../pyproject.toml) funciona com cache, structured output, e retry. Hoje é certo só pra Anthropic e OpenAI; Gemini/DeepSeek/Ollama **nunca foram testados em produção** pelo PeSDR.

**Por quê:**
[src/ai_sdr/llm/factory.py](../../../src/ai_sdr/llm/factory.py) tem 35 linhas e usa `init_chat_model("<provider>:<model>", ...)` — funcionalmente provider-agnostic. Mas testes live ([tests/integration/test_kb_live.py](../../../tests/integration/test_kb_live.py), [tests/integration/test_talkflow_runtime_live.py](../../../tests/integration/test_talkflow_runtime_live.py), [tests/integration/test_objection_live.py](../../../tests/integration/test_objection_live.py)) hoje só rodam contra Anthropic e OpenAI.

Pegadinhas conhecidas que **falham silenciosamente** quando troca de provider:
- **Cache control** ([src/ai_sdr/llm/messages.py](../../../src/ai_sdr/llm/messages.py)) é só Anthropic. Gemini ignora; OpenAI faz auto-cache mas com regra de prefixo.
- **Structured output** com `with_structured_output(model)` — cada provider implementa diferente. Gemini usa "function calling JSON schema" estrito que rejeita Optional types em certos contextos.
- **Token counting** — `tiktoken` `cl100k_base` é OpenAI; Anthropic e Gemini têm tokenizers próprios. Cost estimation fica off.
- **Retry semantics** — error codes diferentes (Gemini retorna 429 com header `Retry-After` diferente).

Antes de prometer "rode com qualquer modelo" pro cliente (que está implícito no design), **validar**.

**Como (esboço):**

1. **Test matrix** em `tests/integration/test_multi_provider_live.py`:

   ```python
   @pytest.mark.live_llm
   @pytest.mark.parametrize("provider,model", [
       ("anthropic", "claude-sonnet-4-6"),
       ("anthropic", "claude-haiku-4-5"),
       ("openai", "gpt-4o-mini"),
       ("google_genai", "gemini-1.5-flash"),
       ("deepseek", "deepseek-chat"),
       ("ollama", "llama3.1:8b"),  # se servidor Ollama disponível
   ])
   async def test_provider_full_turn(provider, model, ...): ...

   async def test_provider_structured_output(...): ...
   async def test_provider_cache_behavior(...): ...
   async def test_provider_error_handling(...): ...
   ```

2. **Documentar limitações descobertas** em `docs/superpowers/specs/2026-05-26-provider-matrix.md` (novo arquivo). Cada provider ganha tabela: "cache supported?", "structured output reliable?", "PT-BR quality benchmark", "cost/turn typical".

3. **Cost estimation refactor** — `src/ai_sdr/observability/pricing.py` (novo) com dict por provider+model. Quando token counter for off (Gemini), usar `response.usage` direto.

**Effort:** `L` (1–2 semanas — incluir custo de API keys reais pra cada provider, debugar diferenças)
**Owner:** Pedro ou Nicolas — Pedro tem aprendizado bom aqui, Nicolas tem contexto histórico do compiler
**Dependências:** 1.2 (LangFuse — facilita comparar quality entre providers)
**Risk se não feito:** cliente troca de provider em produção e quebra silenciosamente
**Done when:**
- [ ] `pytest -m live_llm` passa pra todos os providers documentados como suportados
- [ ] Matriz comparativa publicada em [docs/superpowers/specs/](../specs/)
- [ ] Tenant fixture `tests/fixtures/tenants/` ganha variantes pra cada provider (validável manualmente via simulate)

---

#### 2.3 — Runbook operacional + health check completo

**O quê:**
1. **Runbook**: documento `docs/ops/runbook.md` com **playbooks operacionais** — quando algo dá errado, o que fazer.
2. **Health check completo**: expandir `GET /health` ([src/ai_sdr/api/routes/health.py](../../../src/ai_sdr/api/routes/health.py)) pra retornar status detalhado de cada dependência crítica.

**Por quê:**
Sem runbook, qualquer incidente vira "Pedro/Nicolas em pânico no terminal". Sem health check completo, monitoring externo (UptimeRobot, BetterUptime, etc.) só vê "API responde" — não detecta degradação parcial (Postgres lento, Redis offline, Anthropic com 429).

**Como — Runbook (estrutura mínima):**

`docs/ops/runbook.md`:

```markdown
# PeSDR Operational Runbook

## 1. Como adicionar um novo tenant
   (passo-a-passo: tenant.yaml, secrets.enc.yaml, INSERT na DB, restart, smoke test)

## 2. Como rotacionar tokens de WhatsApp
   (Meta Business Manager → tenant secrets → sops re-encrypt → restart worker)

## 3. Lead marcado como 'unreachable' — como destravar
   (verificar logs do worker, verificar config WhatsApp, manual update no DB se necessário)

## 4. Worker congelado / não processa fila
   (docker logs ai_sdr_worker, verifica advisory lock pendurado, restart processo)

## 5. Custo do tenant atingiu hard_block — como liberar
   (operador valida com cliente; reset manual do Redis key cost:tenant:<id>:<date>)

## 6. Cert SSL expirou ou Traefik não renovou
   (forçar renovação manual; verificar logs Traefik)

## 7. Backup falhou na noite passada
   (debug script; verificar conta B2; restore drill se necessário)

## 8. Migration falha no deploy
   (rollback alembic; investigar; nunca usar `--force`)

## 9. Como fazer restore completo
   (passo-a-passo da seção 0.3, validado)

## 10. Como debugar uma conversa específica (com trace LangFuse)
   (encontrar talkflow_id, abrir no LangFuse, ler trace)
```

**Como — Health check completo:**

```python
# src/ai_sdr/api/routes/health.py
@router.get("/health")
async def health(...) -> dict:
    checks = {
        "db": await _check_db(session),               # ping + version
        "redis": await _check_redis(redis),            # ping
        "arq_pool": "ok" if app.state.arq_pool else "down",
        "checkpointer": "ok" if checkpointer_ready else "down",
        "anthropic": await _ping_anthropic(),          # short request
        # "openai": await _ping_openai(),  # opt-in via flag
    }
    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    code = 200 if status == "ok" else 503
    return JSONResponse({"status": status, "checks": checks}, status_code=code)
```

**Effort:** `M` (3 dias — runbook é o trabalho real, health check é 2-3h)
**Owner:** Pedro (escrever runbook é boa exposição operacional)
**Dependências:** 0.3 (backup), 1.4 (Traefik), 1.2 (LangFuse) — pra cobrir todos os playbooks
**Risk se não feito:** primeiro incidente em prod = pânico
**Done when:**
- [ ] Runbook publicado em `docs/ops/runbook.md`
- [ ] Cada playbook do runbook foi **testado** uma vez (simular incidente, executar playbook, validar resolução)
- [ ] Health check retorna 503 quando Postgres está down (validável: pare o container, espere 5s, curl)
- [ ] UptimeRobot (ou equiv) configurado pra monitorar `/health` a cada 1min

---

#### 2.4 — Security review final (HMAC timing-attack, secrets rotation strategy)

**O quê:** Revisão de segurança focada nos pontos de exposição pública. Não é SOC 2 — é o checklist mínimo antes de expor à internet.

**Checklist:**

1. **HMAC constant-time compare** — verificar [src/ai_sdr/messaging/whatsapp_cloud.py](../../../src/ai_sdr/messaging/whatsapp_cloud.py) que usa `hmac.compare_digest` (não `==`) pra signature check. Se usar `==`, timing attack é viável.

2. **Webhook secret rotation procedure** — quando rotacionar `wa_app_secret` num tenant:
   - Hoje: edita `secrets.enc.yaml`, sops re-encrypt, restart. Mas **descobre erro só quando chega webhook próximo**.
   - Mitigação: aceitar 2 secrets simultaneamente durante transição (1 ativo + 1 standby), com log estruturado de qual matchou.

3. **Anti CSRF / Anti replay** — `external_id` dedupe já protege contra replay simples. Verificar se algum endpoint mutativo aceita GET (nenhum deveria — todos são POST/DELETE).

4. **Secrets em logs** — buscar nos structlog events se algum vaza secrets parcialmente. Pesquisar regex em logs: `wa_token`, `anthropic_key`, etc.

5. **Tenant slug validation** — [src/ai_sdr/schemas/tenant_yaml.py](../../../src/ai_sdr/schemas/tenant_yaml.py) tem `SLUG_RE` validando. Mas em URL `/webhooks/{tenant_slug}/...`, FastAPI route param aceita qualquer string. Adicionar Pydantic validator no path param.

6. **SQL injection** — todo código usa SQLAlchemy ORM ou `text()` com binds (✅). Mas validar com `bandit`/`ruff` rules ativas.

7. **Dependency vulnerabilities** — `uv audit` ou `pip-audit` no CI (adicionar em [0.2](#02--cicd-básico-via-github-actions)).

8. **Container hardening** — rodar containers como non-root (verificar [Dockerfile](../../../Dockerfile)).

9. **CORS** — FastAPI hoje não restringe CORS. Adicionar `CORSMiddleware` com whitelist de origins (relevante quando UI HITL existir).

10. **Body size limit** — webhooks aceitam body de qualquer tamanho? Adicionar limite (1MB suficiente pra WhatsApp).

**Effort:** `M` (2–3 dias — auditoria + fixes pontuais)
**Owner:** Pedro + Nicolas (audit conjunta é melhor; um vê o que o outro perdeu)
**Dependências:** 0.2 (CI pra rodar `bandit`/`pip-audit` automaticamente)
**Risk se não feito:** vulnerabilidade exploitada vira incidente público
**Done when:**
- [ ] Cada item do checklist tem PR ou comentário "verificado, OK" + commit hash de referência
- [ ] `bandit -r src/` retorna zero issues high/critical
- [ ] `uv audit` (ou equiv) retorna zero CVEs critical
- [ ] Documentado em `docs/ops/security-baseline.md`

---

### Critérios "pronto pra produção"

Antes de ligar o primeiro cliente real (Joana Mentora), **todos os itens abaixo** devem estar verdadeiros:

#### Infra (Onda 0)
- [ ] `pyproject.toml` com versões pinadas em libs young
- [ ] `.github/workflows/ci.yml` rodando em PRs
- [ ] `.github/workflows/deploy.yml` rodando em push pra main
- [ ] Backup diário automatizado + 1 restore drill executado

#### Safety & Observability (Onda 1)
- [ ] `LimitsConfig` + cost ceiling implementado e testado
- [ ] LangFuse rodando + dashboards configurados + cost cross-validado
- [ ] Rate limiting em webhooks e endpoints mutativos
- [ ] HTTPS em `sdr.luminai.ia.br` via Traefik

#### Compliance & Readiness (Onda 2)
- [ ] LGPD baseline (disclaimer, retention, delete endpoint)
- [ ] Multi-provider validation matrix passada pra providers que serão usados em prod
- [ ] Runbook publicado e cada playbook validado
- [ ] Security baseline checklist 100% verde

#### Operacional
- [ ] Joana entrou em treinamento de uso (sabe usar `assign-lead`, sabe o que esperar)
- [ ] Plano de comunicação de incidentes definido (Slack? WhatsApp interno? Email?)
- [ ] On-call rotation acordada (mesmo que informal: "Pedro 1ª semana, Nicolas 2ª")

#### Documental
- [ ] `README.md` atualizado com estado atual e quickstart
- [ ] [CLAUDE.md](../../../CLAUDE.md) atualizado com novas seções (cost, LGPD, ops)
- [ ] Este documento atualizado marcando itens fechados ✅

---

## 🟡 PARTE 2 — Otimizações (escala saudável)

> **Princípio:** otimizações **não devem ser feitas preventivamente**. Cada uma tem um **gatilho de escala** específico. Implementar antes do gatilho é over-engineering; implementar depois do gatilho é correr atrás do prejuízo.

> **Como usar esta seção:** revisar mensalmente. Se algum gatilho foi atingido, mover o item correspondente pra "próximo PR". Se não, fica catalogado e quieto.

---

### A · Performance & Scale

#### A.1 — HNSW migration pgvector
**Gatilho:** Qualquer KB ativa em produção passa de **5.000 chunks**.
**O quê:** Migrar índice `kb_chunks_embedding_idx` de `IVFFlat(lists=100)` pra `HNSW(m=16, ef_construction=64)`. HNSW é mais lento pra construir mas tem recall melhor em volumes médios-grandes e degrada mais graciosamente.
**Effort:** `M` (incluindo benchmark recall antes/depois)
**Done when:** Recall@10 igual ou melhor que IVFFlat + p95 query latency dentro de envelope

#### A.2 — Sharding lógico por tenant
**Gatilho:** **30+ tenants ativos** OU `talkflows` table passa de 1M rows.
**O quê:** Sharding por `tenant_id` (hash mod N → DB N). Pattern Citus/Vitess. RLS fica como segunda linha de defesa.
**Effort:** `XL` (3+ semanas — é mudança arquitetural)
**Done when:** 2+ shards rodando + migration zero-downtime documentada

#### A.3 — Re-ranking de chunks com cross-encoder
**Gatilho:** Recall@10 < 70% em queries reais (medido via LangFuse evals).
**O quê:** Após retrieval pgvector, passar top-30 chunks por cross-encoder (e.g. BGE-reranker-v2-m3) e devolver top-10 ranqueado. Adiciona ~200ms mas melhora retrieval em ~15-20%.
**Effort:** `M`
**Done when:** Recall@10 em golden set > 80%

#### A.4 — Postgres replicas pra read scaling
**Gatilho:** P95 de query > 200ms OU `pg_stat_activity` mostra constantes connection waits.
**O quê:** 1 primary + 1+ read replicas. Reads vão pra replica via SQLAlchemy `bind` config. Checkpointer LangGraph e writes ficam no primary. WAL streaming via `pgBackRest` (que também resolve PITR backup).
**Effort:** `L`
**Done when:** P95 query latency cai significativamente; backup PITR validado

#### A.5 — Anthropic Batch API pra reindex offline
**Gatilho:** Reindex KB > 30min OU custo de reindex > $5 por execução.
**O quê:** Para tarefas que não são interativas (reindex de KB, eval batch, classifier warmup), usar [Anthropic Batch API](https://docs.anthropic.com/en/api/creating-message-batches) — 50% mais barato, latency mais alta (até 24h).
**Effort:** `M`
**Done when:** Cost de reindex de KB grande cai pela metade

---

### B · Observabilidade Avançada

#### B.1 — OpenTelemetry distributed tracing
**Gatilho:** Debugar incidente de 1 conversa específica leva > 30min OU 2ª pessoa contratada (precisa correlation cross-service).
**O quê:** OTLP exporter pra Grafana Tempo (self-host na VPS) ou Honeycomb (SaaS). Spans: webhook → ingest → enqueue → worker → step (classifier + main + critic) → adapter.send.
**Effort:** `L`
**Done when:** 1 conversa traceável end-to-end no Tempo/Jaeger UI

#### B.2 — Prometheus + Grafana dashboards
**Gatilho:** Operação tem mais de 1 tenant ativo OU LangFuse não cobre métricas de infra (CPU, memory, queue depth).
**O quê:** Expor `/metrics` em FastAPI (`prometheus-fastapi-instrumentator`), no worker (`arq` tem hooks). Dashboards Grafana: API latency, worker queue depth, DB connection pool, container resource use.
**Effort:** `M`
**Done when:** 3 dashboards saudáveis (API, Worker, Infra) + 5 alertas configurados

#### B.3 — Audit log table
**Gatilho:** Conformidade LGPD avançada (response à autoridade) OU 1ª tentativa de tampering detectada.
**O quê:** Tabela `audit_log` (insert-only, RLS) capturando operações sensíveis: `lead.assigned`, `lead.deleted`, `tenant.config_changed`, `secret.rotated`. Imutável (PostgreSQL `pg_partman` ou tabela com `INSERT`-only role).
**Effort:** `M`

#### B.4 — Alerting via PagerDuty/Opsgenie/email
**Gatilho:** Equipe operacional não revisa logs/dashboards proativamente OU 1 incidente passou despercebido > 1h.
**O quê:** Alertmanager → email + Telegram/Slack. Alertas: cost_ceiling.hard_block, db.down, worker.queue_depth > 100, latency.p95 > 5s, cert.expiring_in_7d.
**Effort:** `S` (depois de B.2)

---

### C · Maturidade Multi-Tenant

#### C.1 — Auto-criação de custom fields no CRM (RDStation)
**Gatilho:** Onboarding manual de tenant > 30min por causa de "criar custom fields no RDStation".
**O quê:** Durante onboarding, ler `tenant.crm.field_mapping`, criar fields via RDStation API automaticamente.
**Effort:** `M` (depende de Plano 7 estar pronto)

#### C.2 — UI admin pra adicionar tenant
**Gatilho:** Pedro/Nicolas gasta > 1h por mês em "add tenant via psql".
**O quê:** Endpoint REST `POST /admin/tenants` + UI mínima (Plano 11 UI cobre).
**Effort:** `L`

#### C.3 — Per-tenant feature flags
**Gatilho:** Querer testar feature nova num tenant específico sem afetar outros.
**O quê:** Tabela `tenant_features` ou bloco `features:` em `tenant.yaml`. Code: `if tenant.features.classifier_v2: ...`.
**Effort:** `M`

#### C.4 — White-label de webhooks/UI
**Gatilho:** Cliente Vialum ou enterprise pedir.
**O quê:** Domínio + cert por tenant (`joana.sdr.com.br` em vez de `sdr.luminai.ia.br/webhooks/joana/...`). Traefik aceita wildcards.
**Effort:** `L`

---

### D · Feature Breadth (próximos planos)

> **Ordem natural pós-MVP** (segue spec [§21](../specs/2026-05-21-ai-sdr-design.md) + [ADR](../specs/2026-05-24-adapter-pattern-decision.md)).

#### D.1 — Plano 6: Identity Resolver
**Gatilho:** Quando começar Plano Vialum-integration OU quando 2º messaging channel entrar (Instagram, email).
**O quê:** Formalizar `find_or_create_lead_by_address` ([src/ai_sdr/messaging/ingest.py](../../../src/ai_sdr/messaging/ingest.py)) → interface `IdentityResolver` + impl `InternalLead` (default) + futuro `VialumHubAdapter`.
**Effort:** `M`

#### D.2 — Plano 7: CRM (RDStation)
**Gatilho:** Joana usar RDStation (sim, ela usa). É o próximo plano natural pós-MVP.
**O quê:** `CRMAdapter` ABC + `RDStationAdapter`. Webhook `/webhooks/crm/{provider}`. Sync bidirecional (state + custom fields). Anti-loop via `source` metadata.
**Effort:** `L`

#### D.3 — Plano 8: Media (Whisper, Vision, ElevenLabs)
**Gatilho:** Quando lead começar a mandar áudio/imagem com volume relevante.
**O quê:** Whisper (audio inbound), Anthropic Vision (image), ElevenLabs (audio outbound). Extensão aditiva ao `MessagingAdapter`.
**Effort:** `L`

#### D.4 — Plano 9: Follow-up Scheduler
**Gatilho:** > 30% dos leads ficam silenciosos sem follow-up (medido via LangFuse + DB query).
**O quê:** Worker arq cron processando TalkFlows pausados. Consome `WindowExpiredError` ([src/ai_sdr/messaging/errors.py](../../../src/ai_sdr/messaging/errors.py)) pra disparar template HSM.
**Effort:** `L`

#### D.5 — Plano 11: HITL UI
**Gatilho:** Operador (Joana) atribuir 5+ leads/dia via CLI vira fricção.
**O quê:** Web app pra revisar `pending_assignment`, aprovar/editar responses bloqueados pelos guardrails. Consome endpoints existentes em [api/routes/leads.py](../../../src/ai_sdr/api/routes/leads.py).
**Effort:** `XL` (frontend completo)

#### D.6 — Plano Vialum-integration
**Gatilho:** Decisão de ativar PeSDR pra clientes do Vialum.
**O quê:** `VialumChatAdapter` + `VialumHubAdapter` + `VialumTasksInboxAdapter`. ADR já especifica.
**Effort:** `L`

#### D.7 — A/B testing de TreeFlows
**Gatilho:** Joana ou cliente Vialum querer experimentar variantes (mudança de prompt, mudança de fluxo).
**O quê:** Variant assignment determinístico por lead_id, métricas segmentadas por variant_id, dashboard de comparação.
**Effort:** `L`

#### D.8 — Streaming de LLM responses
**Gatilho:** Plano 11 (HITL UI) começar — operador esperando 5s/turno é UX ruim.
**O quê:** Refactor de `_invoke_inner` em [compiler.py](../../../src/ai_sdr/treeflow/compiler.py) pra usar `.astream()`. WhatsApp não consome streaming nativamente — só UI HITL e CLI debug.
**Effort:** `M`

---

### E · Prevenção de Tech Debt

#### E.1 — Refactor preventivo do `compiler.py`
**Gatilho:** [compiler.py](../../../src/ai_sdr/treeflow/compiler.py) passa de 800 linhas (hoje: 580).
**O quê:** Quebrar em pacote `treeflow/compiler/` com sub-módulos (`nodes.py`, `classifier.py`, `routing.py`, `kb.py`, `objections.py`). Mantém API `compile_treeflow()` mas implementação distribuída.
**Effort:** `M`

#### E.2 — LangGraph 1.x migration
**Gatilho:** LangGraph 1.0 estável é lançado E ganha features que valem o esforço (e.g. melhor checkpointing, melhor observability).
**O quê:** Bump major + adaptar `Command`, `add_conditional_edges`, sentinels.
**Effort:** `L`

#### E.3 — Multi-provider embeddings
**Gatilho:** Querer trocar de OpenAI por Voyage AI (`voyage-3`) ou Cohere (PT-BR multilingual) — ambos têm benchmarks superiores em pt-BR (potencialmente).
**O quê:** Expandir `EmbeddingsConfig` literal pra free-form. **Reindex completo** necessário (dim 1536 != dim 1024).
**Effort:** `M` + custo de reindex

#### E.4 — WhatsApp BSP adapter (Take Blip / Gupshup)
**Gatilho:** 5+ tenants WhatsApp OU 1ª deprecation de endpoint Meta que afetar prod.
**O quê:** Novo `MessagingAdapter` impl pra Take Blip BR (provedor brasileiro) ou Gupshup. Adapter pattern protege — só plug.
**Effort:** `M`

---

### F · Compliance & Governança

#### F.1 — LGPD complete (DPIA, encryption at rest)
**Gatilho:** > 1000 leads únicos OR cliente B2B exigir.
**O quê:** DPIA formal, encryption at rest (LUKS na VPS ou EBS encrypted), termos de uso publicados, política de privacidade.
**Effort:** `L`

#### F.2 — SOC 2 readiness
**Gatilho:** Cliente enterprise exigir.
**O quê:** Trabalho contínuo de processo. Não é técnico apenas — exige documentação, change management, vendor mgmt. Tipicamente 6-12 meses de prep.
**Effort:** `XL`

#### F.3 — Multi-region deploy
**Gatilho:** Latência de cliente em outra região (EU?) inaceitável.
**O quê:** Stack replicada em outra região. Postgres logical replication ou multi-master. Complicado.
**Effort:** `XL`

---

## Anexos

### Anexo A — Versões a pinar/upgradar

| Lib | Versão atual ([pyproject.toml](../../../pyproject.toml)) | Recomendação | Risk de bump |
|---|---|---|---|
| `langgraph` | `>=0.2.60` | **Pinnar em `==0.2.60`** | Alto (API churn pré-1.0) |
| `langgraph-checkpoint-postgres` | `>=2.0.21` | **Pinnar em `==2.0.21`** | Alto |
| `langchain` | `>=1.3.0` | **Pinnar em `==1.3.0`** | Médio |
| `langchain-core` | `>=0.3.28` | **Pinnar em `==0.3.28`** | Médio |
| `langchain-anthropic` | `>=0.3.0` | **Pinnar em `==0.3.0`** | Alto (cache_control format) |
| `langchain-openai` | `>=0.2.14` | **Pinnar em `==0.2.14`** | Médio |
| `langchain-google-genai` | `>=2.0.0` | **Pinnar em `==2.0.0`** | Médio |
| `langchain-deepseek` | `>=0.1.0` | **Pinnar em `==0.1.0`** | Alto (lib jovem) |
| `langchain-ollama` | `>=0.2.0` | **Pinnar em `==0.2.0`** | Alto |
| `fastapi` | `>=0.115` | Mantém `>=` (estável) | Baixo |
| `sqlalchemy` | `>=2.0.36` | Mantém `>=` (estável) | Baixo |
| `pydantic` | `>=2.9` | Mantém `>=` (estável) | Baixo |
| `arq` | `>=0.26` | Mantém `>=` (estável) | Baixo |
| `pgvector` | `>=0.3.6` | Mantém `>=` (estável) | Baixo |

### Anexo B — Métricas-gatilho

Quando qualquer destas métricas for atingida, **revisar a otimização correspondente**:

| Métrica | Threshold | Otimização disparada |
|---|---|---|
| KB ativa # chunks | > 5.000 | [A.1 HNSW](#a1--hnsw-migration-pgvector) |
| Tenants ativos | > 30 | [A.2 Sharding](#a2--sharding-lógico-por-tenant) |
| Recall@10 em queries reais | < 70% | [A.3 Re-ranking](#a3--re-ranking-de-chunks-com-cross-encoder) |
| Postgres P95 query | > 200ms | [A.4 Replicas](#a4--postgres-replicas-pra-read-scaling) |
| Reindex KB time | > 30min | [A.5 Batch API](#a5--anthropic-batch-api-pra-reindex-offline) |
| MTTR de incidente | > 30min | [B.1 OpenTel](#b1--opentelemetry-distributed-tracing) |
| Tenants ativos | > 1 | [B.2 Prometheus](#b2--prometheus--grafana-dashboards) |
| Áudio/imagem inbound | > 10% do volume | [D.3 Media](#d3--plano-8-media-whisper-vision-elevenlabs) |
| Lead silencioso sem follow-up | > 30% | [D.4 Follow-up](#d4--plano-9-follow-up-scheduler) |
| Leads atribuídos via CLI | > 5/dia | [D.5 HITL UI](#d5--plano-11-hitl-ui) |
| Cliente enterprise pedir | (sim/não) | [F.1 LGPD complete](#f1--lgpd-complete-dpia-encryption-at-rest), [F.2 SOC 2](#f2--soc-2-readiness) |
| `compiler.py` LoC | > 800 | [E.1 Refactor](#e1--refactor-preventivo-do-compilerpy) |

### Anexo C — Glossário

| Termo | Significado |
|---|---|
| **PeSDR** | "Pedro Smart Development Resource" / "Plataforma SDR" — nome do projeto |
| **SDR** | Sales Development Representative (qualificador de lead) |
| **TreeFlow** | Definição estática de funil (YAML, versionada) |
| **TalkFlow** | Instância viva de conversa percorrendo um TreeFlow |
| **Node** | Estágio dentro de um TreeFlow |
| **RLS** | Row-Level Security (Postgres) — isolamento por `tenant_id` |
| **HSM** | Highly Structured Message (template aprovado pela Meta WhatsApp) |
| **BSP** | Business Solution Provider (intermediário oficial Meta — Take Blip, Gupshup, Twilio, Vonage) |
| **HNSW** | Hierarchical Navigable Small World — algoritmo de vector index alternativo ao IVFFlat |
| **IVFFlat** | Inverted File with Flat — algoritmo de vector index simples baseado em clusters |
| **DPIA** | Data Protection Impact Assessment (LGPD) |
| **PITR** | Point-In-Time Recovery (backup com replay de WAL) |
| **WAL** | Write-Ahead Log (Postgres) |
| **MTTR** | Mean Time To Recovery (média de tempo pra resolver incidente) |
| **Critic pass** | 2º LLM (Haiku) revisa response do main LLM antes de enviar |
| **Inline objection response** | Resposta gerada na mesma node sem deflectar pra sub-node |
| **Sub-node objection** | Objection que vira detour pra outro node completo, depois `BACK_TO_ORIGIN` |

### Anexo D — Decisões pendentes (Pedro ↔ Nicolas)

Itens **não resolvidos** neste documento que precisam de decisão conjunta:

1. **Owner da Onda 0** — quem faz CI/CD (item 0.2)? Sugerido Pedro, mas Nicolas tem mais contexto histórico.
2. **Storage de backup** — Backblaze B2 ou Hetzner Object Storage? Ambos funcionam; Backblaze é mais barato pra cold storage, Hetzner é mais simples se já temos conta Hetzner.
3. **LangFuse self-host vs SaaS** — self-host (item 1.2) economiza dinheiro mas adiciona container. Confirmar disposição de operar mais 2 containers (langfuse + langfuse-db).
4. **Comunicação de incidentes** — Slack/WhatsApp interno/email/PagerDuty? Decisão informa B.4.
5. **Quando ligar Joana em prod** — qual semana esperada? Ajuda priorizar quais itens cortar (mas não recomendamos cortar nenhum dos 10 urgentes).
6. **Frequência de revisão deste doc** — sugerido **mensalmente**, sexta da 1ª semana do mês. Confirmar.
7. **Cost ceiling default** — `max_usd_per_day=50` no spec é razoável pra dev. Pra prod com Joana, qual o teto realista? Depende de volume esperado de leads.
8. **Provider matrix prioritários** — quais providers vamos efetivamente testar end-to-end (item 2.2)? Anthropic + OpenAI são certos. Gemini? DeepSeek? Ollama é dev only?
9. **CORS whitelist** (item 2.4 #9) — quais origins permitir? Provavelmente só `https://*.luminai.ia.br` e `localhost` para dev.
10. **Versionamento deste documento** — manter histórico de versões via commits (preferido) OU criar `2026-06-XX-production-readiness.md` quando ondas fecharem (arquivo novo)?

---

## Histórico de revisões

| Data | Autor | Mudança |
|---|---|---|
| 2026-05-26 | Pedro (Claude-assist) | Versão inicial — Planos 1-5 fechados, 10 itens urgentes + 18 otimizações |

---

**Fim do documento.**

> Este documento será revisado mensalmente (1ª sexta do mês) ou sempre que uma das ondas urgentes fechar. Markar itens fechados com ✅, mover otimizações ativadas pra "em execução", arquivar itens obsoletos com strikethrough mas mantendo no histórico.
