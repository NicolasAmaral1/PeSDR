# KB + Guardrails — Design Spec (Plano 3)

**Data:** 2026-05-23
**Status:** Draft para revisão (pós-brainstorm)
**Autor:** Nicolas Amaral (brainstorm com Claude)
**Parent spec:** [`2026-05-21-ai-sdr-design.md`](./2026-05-21-ai-sdr-design.md) (§4.5 Guardrails, §4.7 KB Retrievers, §11/§19 schema)
**Plano antecessor:** [Plano 2 — TreeFlow Engine + LangGraph](../plans/2026-05-22-treeflow-engine-langgraph.md)

---

## 1. Resumo executivo

Adiciona à plataforma duas camadas: **Knowledge Base (RAG via pgvector)** e **Guardrails (anti-alucinação)**. KB permite que cada Node de TreeFlow consulte chunks de documentos `.md` indexados, injetando-os no prompt do LLM como referência factual. Guardrails valida o `response_text` gerado contra uma whitelist por tenant (preços/produtos permitidos) e, opcionalmente, dispara um critic pass (segundo LLM) em nodes marcados `critical: true`. Quando whitelist ou critic bloqueiam, o sistema retenta até 2x com feedback explícito; se ainda falhar, emite fallback genérico configurado pelo tenant.

A arquitetura é projetada para **substituição futura do fallback genérico por HITL** (human-in-the-loop via LangGraph `interrupt()`) sem refactor maior.

---

## 2. Escopo

### 2.1 Dentro do Plano 3

- Schema pgvector (`kb_documents`, `kb_chunks` com RLS + IVFFlat).
- Chunker markdown-aware com cap em 600 tokens por chunk.
- Embedder OpenAI `text-embedding-3-small` (1536d).
- Indexer idempotente via `content_hash` por documento.
- CLI `ai-sdr reindex-kb --tenant <slug> [--kb <id>] [--prune]`.
- Retriever pgvector com filtro por tenant + `kb_id`, `top_k`, `min_score`.
- Injeção da KB como `SystemMessage` separado (preserva cache do `node.prompt`).
- Whitelist validator via extensão do structured output (`prices_mentioned: list[int]`, `products_mentioned: list[str]`).
- Critic pass (Haiku) configurável por node (`critical: true`).
- Retry loop com feedback explícito (máx 2 tentativas).
- Fallback genérico configurado em `tenant.yaml`.
- **Prompt caching** Anthropic (5-min ephemeral) com toggle por tenant.
- Telemetria estruturada (`kb.indexed`, `kb.retrieved`, `kb.no_match`, `guardrail.blocked`, `guardrail.fallback_used`, `critic.flagged`, `critic.fallback_used`).
- Cobertura de testes (unit + integration + 1 live_llm).

### 2.2 Fora do Plano 3 (non-goals explícitos)

| Item | Por quê | Quando |
|---|---|---|
| HITL escalation (humano aprova/edita/rejeita response bloqueado) | Exige UI de review; LangGraph `interrupt()` resolve a parte de backend mas falta o frontend | Plano N (após WhatsApp + CRM, quando há fluxo real de leads) |
| Retrieval com history-context (embed das últimas N mensagens, não só `user_input`) | Limitação real (lead diz "e a outra?" → embed vira lixo); resolvível com 1 linha em `retriever.py` | Após medir cache miss / mau retrieval em produção |
| Re-ranking de chunks (cross-encoder) | Recall do `text-embedding-3-small` é OK pra volume MVP; re-rank adiciona latência | Se KB crescer > 10k chunks |
| 1-hora extended cache | 5-min cobre conversação ativa; lead idle não cacheia mesmo | Se medir cache miss alto em padrão "lead some 20min e volta" |
| History caching (cachear turnos passados) | Ganho marginal (~250 tok/turn); exige reposicionar KB block | Plano de otimização futura |
| Reindex automático (watcher, lifespan, CI hook) | CLI manual cobre dev + post-deploy script | Se a manualidade incomodar na operação |
| Auto-criação de KB via UI / endpoint admin | Sem UI no MVP | V2 do produto (junto com admin web do spec §21) |
| Cost analytics dashboard (cost/tenant/turn pra KB + guardrails) | Telemetria já emite eventos; dashboard fica pro Plano de observabilidade | Plano de observabilidade futuro |
| Whitelist semântica (e.g. bloquear promessa de "garantia vitalícia") | Critic pass cobre parcialmente; whitelist hoje é só de valores/strings | Conforme demanda |

---

## 3. Arquitetura

### 3.1 File layout (delta sobre o estado pós-Plano 2)

```
src/ai_sdr/
├── kb/                                  # NEW package
│   ├── __init__.py
│   ├── chunker.py                       # MarkdownChunker(max_tokens=600) → list[ChunkDraft]
│   ├── embeddings.py                    # build_embedder(secrets) → Embedder
│   ├── indexer.py                       # reindex_tenant_kb(session, tenant, kb_root, prune=False)
│   └── retriever.py                     # retrieve(session, tenant_id, kb_refs, query) → list[RetrievedChunk]
│
├── guardrails/                          # NEW package
│   ├── __init__.py
│   ├── schemas.py                       # Verdict (passed: bool, reason, suggested_fix)
│   ├── whitelist.py                     # validate_whitelist(extracted, guardrails) → Verdict
│   ├── critic.py                        # critic_pass(llm_factory, response, kb_chunks, history, guardrails) → Verdict
│   └── runner.py                        # run_with_guardrails(inner, …) — orquestra retry + fallback
│
├── llm/
│   ├── extractor.py                     # MODIFIED: build_structured_model() aceita guardrails opcional
│   └── messages.py                      # NEW: build_system_messages(static, dynamic_blocks, provider)
│
├── models/                              # NEW SQLAlchemy models
│   ├── kb_document.py                   # kb_documents
│   └── kb_chunk.py                      # kb_chunks
│
├── schemas/
│   ├── tenant_yaml.py                   # MODIFIED: GuardrailsConfig + LLMDefaults.cache_enabled
│   └── treeflow_yaml.py                 # MODIFIED: NodeSpec.knowledge_base agora list[KBRef] tipado
│
├── treeflow/
│   ├── compiler.py                      # MODIFIED: node_fn injeta KB + wrappa em run_with_guardrails
│   └── loader.py                        # MODIFIED: warning se prompt < 1024 tok com cache enabled
│
└── cli/
    ├── app.py                           # MODIFIED: adiciona subcommand reindex-kb
    └── reindex_kb.py                    # NEW: typer command

migrations/versions/
└── 0005_kb_tables.py                    # NEW: kb_documents + kb_chunks + RLS + IVFFlat

tenants/example/
├── tenant.yaml                          # MODIFIED: adiciona guardrails: + llm.cache_enabled
├── treeflows/example.yaml               # MODIFIED: 1 node ganha knowledge_base ref
└── kb/                                  # NEW
    └── example_kb/
        └── precos.md                    # fixture de testes

tests/
├── unit/
│   ├── test_kb_chunker.py               # NEW
│   ├── test_kb_yaml_schema.py           # NEW
│   ├── test_whitelist_validator.py      # NEW
│   ├── test_guardrails_runner.py        # NEW (FakeListChatModel)
│   └── test_messages_builder.py         # NEW (per-provider system message format)
└── integration/
    ├── test_kb_indexer.py               # NEW
    ├── test_kb_retriever.py             # NEW (com RLS)
    ├── test_kb_indexer_cli.py           # NEW (subprocess)
    ├── test_compiler_with_kb_and_guardrails.py  # NEW
    ├── test_guardrails_critic.py        # NEW
    └── test_kb_live.py                  # NEW (@pytest.mark.live_llm — embed real)
```

### 3.2 Integração com Plano 2

| Componente Plano 2 | Mudança no Plano 3 |
|---|---|
| `compiler._make_node_fn` (linhas 79-118) | Adiciona pré-LLM (retrieval) e wrappa LLM call em `run_with_guardrails` |
| `extractor.build_structured_model(collects)` | Opt-in param `guardrails: GuardrailsConfig \| None` adiciona 2 campos auto |
| `loader.TreeFlowLoader.load()` | Pós-validação: warning se `node.prompt` curto e `cache_enabled` |
| `runtime.TalkFlowRuntime` | Sem mudança — toda complexidade fica no compiler |
| `schemas.tenant_yaml.LLMDefaults` | Adiciona `cache_enabled: bool = True` |
| `schemas.tenant_yaml.TenantConfig` | Adiciona `guardrails: GuardrailsConfig \| None = None` |
| `schemas.treeflow_yaml.NodeSpec.knowledge_base` | De `list[dict] \| None` (opaco forward-compat) para `list[KBRef] \| None` tipado |
| `schemas.treeflow_yaml.NodeSpec.critical` | Já existia (bool default false); agora ATIVO (compiler respeita) |

---

## 4. Componentes

### 4.1 `kb/chunker.py`

```python
@dataclass(frozen=True)
class ChunkDraft:
    idx: int                # ordem dentro do documento
    heading_path: str | None  # "Preços > Mentoria" ou None se sem headers
    content: str
    token_count: int


class MarkdownChunker:
    def __init__(self, max_tokens: int = 600) -> None: ...

    def split(self, content_md: str) -> list[ChunkDraft]:
        """Split markdown into chunks respecting ## boundaries; cap at max_tokens.

        Strategy:
        - Parse `.md` por headers (`#`, `##`, `###`, etc.)
        - Cada seção (heading + corpo) é um candidato a chunk
        - Se seção > max_tokens, parte por parágrafo (`\n\n`) acumulando até cap
        - heading_path acumula breadcrumb (ex: "FAQ > Preços > Mentoria")
        - Token count via tiktoken (cl100k_base, encoding do text-embedding-3-small)
        """
```

**Edge cases:**
- `.md` sem nenhum header: 1 chunk só, `heading_path=None`, partido em parágrafos se > cap.
- Header sem corpo: skip (não vira chunk vazio).
- Parágrafo único > cap: trunca por sentence (split por `. `, agrupa até cap).
- Conteúdo vazio: retorna `[]`.

### 4.2 `kb/embeddings.py`

```python
class Embedder:
    def __init__(self, client: openai.AsyncOpenAI, model: str = "text-embedding-3-small") -> None: ...
    async def embed_query(self, text: str) -> list[float]: ...
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding. OpenAI API aceita até 2048 inputs por request; paginado se exceder."""


def build_embedder(secrets: dict[str, str], model: str = "text-embedding-3-small") -> Embedder:
    """Factory mirroring llm/factory.py pattern. Requires 'openai_key' in secrets."""
```

Dimension: 1536 (default do `text-embedding-3-small`). Coluna `vector(1536)` no schema.

### 4.3 `kb/indexer.py`

```python
@dataclass
class IndexResult:
    indexed: list[str]        # doc_paths re-indexados
    skipped: list[str]        # doc_paths sem mudança
    pruned: list[str]         # doc_paths removidos (se prune=True)
    failed: list[tuple[str, str]]  # (doc_path, error_message)


async def reindex_tenant_kb(
    session: AsyncSession,
    tenant: Tenant,
    kb_root: Path,              # default Path("kb")
    embedder: Embedder,
    chunker: MarkdownChunker,
    prune: bool = False,
    kb_id: str | None = None,   # filtrar pra 1 KB só
) -> IndexResult:
    """Walk kb_root/<tenant.slug>/<kb_id>/*.md, idempotent reindex.

    1. Lista .md no FS sob kb_root/<slug>/[<kb_id>/]**/*.md
    2. Pra cada doc: sha256(content) → compara com kb_documents.content_hash
    3. Se diferente (ou row missing): DELETE kb_chunks WHERE document_id, re-chunk, embed, UPSERT
    4. Se prune=True: deleta kb_documents que existem no DB mas não no FS
    """
```

**RLS:** chama `set_tenant_context(session, tenant.id)` no topo. Indexer só vê/escreve KBs do tenant em questão. CLI itera tenants se quiser multi-tenant.

### 4.4 `kb/retriever.py`

```python
@dataclass(frozen=True)
class KBRef:
    """Mirror do schema YAML (também declarado em schemas/treeflow_yaml.py)."""
    id: str
    top_k: int = 3
    min_score: float = 0.7


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    heading_path: str | None
    kb_id: str
    score: float              # 1 - cosine_distance (0..1, maior = mais similar)


async def retrieve(
    session: AsyncSession,
    tenant_id: UUID,
    kb_refs: list[KBRef],
    query: str,
    embedder: Embedder,
) -> list[RetrievedChunk]:
    """Embed query, run single SQL with kb_id IN (...), filter by min_score, top_k global.

    NOTA: top_k aqui é o agregado entre todas as KBs referenciadas, não por KB.
    Se cada KBRef tiver top_k diferente, usa max() — simplificação pro MVP.
    Min_score é aplicado como filtro pós-query (não no SQL).
    """
```

**SQL gerada (psycopg3 / SQLAlchemy):**

```sql
SELECT id, content, heading_path, kb_id,
       1 - (embedding <=> :query_vec) AS score
FROM kb_chunks
WHERE tenant_id = :tenant_id
  AND kb_id = ANY(:kb_ids)
ORDER BY embedding <=> :query_vec ASC
LIMIT :top_k_max
```

Pós-processamento Python: filtra `score >= min_score` (usa o `min_score` do `KBRef` correspondente por chunk via `kb_id`). RLS já aplicado via `set_tenant_context`.

### 4.5 `guardrails/whitelist.py`

```python
def validate_whitelist(
    prices_mentioned: list[int],
    products_mentioned: list[str],
    guardrails: GuardrailsConfig,
) -> Verdict:
    """Retorna Verdict(passed=True) se TUDO mencionado está nas listas allowed.

    Se algo fora: passed=False, reason explica, suggested_fix = msg pro LLM retentar.
    """
```

**`Verdict` (em `guardrails/schemas.py`):**

```python
class Verdict(BaseModel):
    """Pydantic BaseModel (não dataclass) pra ser compatível com
    LangChain.with_structured_output usado em critic_pass."""
    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str | None = None
    suggested_fix: str | None = None   # mensagem pra adicionar ao retry como SystemMessage
```

**Política:** comparação case-insensitive em `products_mentioned`; exact match em `prices_mentioned`. Se `guardrails.enabled=False` (ou config ausente), retorna `passed=True` sempre (no-op).

### 4.6 `guardrails/critic.py`

```python
async def critic_pass(
    llm_factory: LLMFactory,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    response_text: str,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],     # últimas 4 messages (alvo: ~500 tok)
    guardrails: GuardrailsConfig,
) -> Verdict:
    """Critic pass usa tenant_llm.classifier (Haiku) por design (cheap)."""
```

**Critic prompt (template):**

```
Você é um revisor de qualidade de respostas de um SDR (assistente comercial).
Receba a RESPOSTA proposta pelo agente, o CONTEXTO FACTUAL (chunks de KB) e
as REGRAS COMERCIAIS (preços/produtos permitidos).

Rejeite se a resposta:
1. Mencionar valor (R$) ou produto NÃO listado nas regras
2. Fizer promessa não suportada pelo CONTEXTO FACTUAL (e.g. "garantia vitalícia"
   se não tiver isso na KB)
3. Inventar dado factual (data, prazo, condição) não citado no CONTEXTO

Caso contrário, aprove.

Retorne JSON:
{
  "passed": bool,
  "reason": str | null,           # se passed=false
  "suggested_fix": str | null     # mensagem pro agente refazer
}

---
REGRAS COMERCIAIS:
{guardrails as YAML}

CONTEXTO FACTUAL:
{kb_chunks renderizados com heading_path}

HISTÓRICO RECENTE:
{recent_history últimas 4 msgs}

RESPOSTA PROPOSTA:
{response_text}
```

LLM call via `with_structured_output(Verdict)`. Latency adicional: ~500-1000ms Haiku.

### 4.7 `guardrails/runner.py`

```python
@dataclass
class GuardrailsRunResult:
    response_text: str
    collected: dict[str, Any]
    blocked: bool                 # True se fallback foi usado
    attempts: int                 # 0 = passou na primeira; 1 = 1 retry; etc.


async def run_with_guardrails(
    *,
    inner: Callable[[list[BaseMessage]], Awaitable[ExtractResult]],
    base_messages: list[BaseMessage],
    guardrails: GuardrailsConfig | None,
    critical: bool,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    llm_factory: LLMFactory,
    max_retries: int = 2,
) -> GuardrailsRunResult:
    """Run inner, validate, retry with feedback, fallback if exhausted.

    Fluxo:
      attempt = 0
      messages = base_messages
      while attempt <= max_retries:
        result = await inner(messages)

        if guardrails and guardrails.enabled:
          verdict_w = validate_whitelist(
            result.prices_mentioned, result.products_mentioned, guardrails)
          if not verdict_w.passed:
            log("guardrail.blocked", reason=verdict_w.reason, attempt=attempt)
            messages = base_messages + [SystemMessage(verdict_w.suggested_fix)]
            attempt += 1
            continue

        if critical and guardrails and guardrails.critic_enabled:
          verdict_c = await critic_pass(llm_factory, tenant_llm, secrets,
                                        response_text=result.response_text,
                                        kb_chunks=kb_chunks,
                                        recent_history=recent_history,
                                        guardrails=guardrails)
          if not verdict_c.passed:
            log("critic.flagged", reason=verdict_c.reason, attempt=attempt)
            messages = base_messages + [SystemMessage(verdict_c.suggested_fix)]
            attempt += 1
            continue

        return GuardrailsRunResult(
          response_text=result.response_text,
          collected=result.collected,
          blocked=False,
          attempts=attempt,
        )

      # exhausted — chama hook isolado
      return _handle_exhausted(guardrails, last_verdict=verdict_w or verdict_c)


def _handle_exhausted(
    guardrails: GuardrailsConfig,
    last_verdict: Verdict,
) -> GuardrailsRunResult:
    """Hook isolado pra HITL futuro substituir.

    Hoje: fallback genérico do tenant.guardrails.fallback_text.
    Futuro (Plano HITL): persist_pending_review + raise GraphInterrupt.
    """
    log("guardrail.fallback_used", reason=last_verdict.reason)
    return GuardrailsRunResult(
      response_text=guardrails.fallback_text,
      collected={},
      blocked=True,
      attempts=max_retries + 1,
    )
```

### 4.8 `llm/messages.py`

```python
def build_system_messages(
    static_prompt: str,
    dynamic_blocks: list[str],
    provider: Literal["anthropic", "openai"],
    cache_enabled: bool = True,
) -> list[SystemMessage]:
    """Build system messages with provider-appropriate caching.

    Anthropic + cache_enabled: SystemMessage com content list, primeiro bloco
      tem cache_control={"type": "ephemeral"}.
    Anthropic + not cache_enabled: SystemMessage com content list, sem cache_control.
    OpenAI: concatena tudo num único SystemMessage (auto-cache pelo provider).
    """
```

### 4.9 `llm/extractor.py` (modificação)

```python
def build_structured_model(
    collects: list[CollectField],
    guardrails: GuardrailsConfig | None = None,   # NEW
) -> type[BaseModel]:
    """Quando guardrails está presente E enabled, adiciona ao Pydantic gerado:
      - prices_mentioned: list[int] = []
      - products_mentioned: list[str] = []
    Description do field instrui LLM: "liste TODOS os valores/produtos que
    você mencionou textualmente na response_text".
    """
```

---

## 5. YAML schemas

### 5.1 `tenant.yaml`

```yaml
# ... campos existentes ...

llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.7
    api_key_ref: anthropic_key
  classifier:                          # USADO pelo critic pass
    provider: anthropic
    model: claude-haiku-4-5
    api_key_ref: anthropic_key
  embeddings:                          # NEW
    provider: openai
    model: text-embedding-3-small
    api_key_ref: openai_key
  cache_enabled: true                  # NEW (default true)

guardrails:                            # NEW (opcional — se omitido, tudo desligado)
  enabled: true
  allowed_prices: [247, 1497, 1997, 2000, 6000]
  allowed_products: ["Mentoria", "Aceleradora", "Downsell"]
  critic_enabled: true                 # se false, ignora node.critical=true
  fallback_text: "Deixa eu confirmar esse valor com a equipe e já te respondo, ok?"
  max_retries: 2                       # default 2
```

**Pydantic (`schemas/tenant_yaml.py`):**

```python
class EmbeddingsConfig(BaseModel):
    provider: Literal["openai"]
    model: str = "text-embedding-3-small"
    api_key_ref: str = "openai_key"


class LLMDefaults(BaseModel):
    default: LLMConfig
    classifier: LLMConfig | None = None
    embeddings: EmbeddingsConfig | None = None   # NEW
    cache_enabled: bool = True                    # NEW


class GuardrailsConfig(BaseModel):                # NEW
    enabled: bool = True
    allowed_prices: list[int] = Field(default_factory=list)
    allowed_products: list[str] = Field(default_factory=list)
    critic_enabled: bool = True
    fallback_text: str = Field(min_length=10)
    max_retries: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def _validate_lists_when_enabled(self) -> Self:
        if self.enabled and not self.allowed_prices and not self.allowed_products:
            raise ValueError("guardrails.enabled=true requires non-empty allowed_prices or allowed_products")
        return self


class TenantConfig(BaseModel):
    # ... existing ...
    guardrails: GuardrailsConfig | None = None    # NEW
```

### 5.2 `tenants/<slug>/treeflows/<id>.yaml`

```yaml
nodes:
  - id: "oferta_mentoria"
    prompt: |
      Você é a SDR da Joana...
      (tom, persona, instruções — pelo menos 1024 tokens pra ativar cache)
    knowledge_base:                    # tipado agora
      - id: kb_oferta_mentoria
        top_k: 3                       # default 3
        min_score: 0.7                 # default 0.7
    critical: true                     # ativa critic pass (default false)
    exit_condition: ...
    next_nodes: ...
```

**Pydantic (`schemas/treeflow_yaml.py`):**

```python
class KBRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)


class NodeSpec(BaseModel):
    # ... existing ...
    knowledge_base: list[KBRef] | None = None   # NOW typed (was list[dict])
    critical: bool = False                       # já existia, agora ATIVO
```

---

## 6. Schema do banco

### 6.1 `kb_documents`

```sql
CREATE TABLE kb_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kb_id           TEXT NOT NULL,
    doc_path        TEXT NOT NULL,
    content_hash    TEXT NOT NULL,                   -- sha256 do .md cru
    content_md      TEXT NOT NULL,                   -- snapshot pra audit + re-chunk sem FS
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, kb_id, doc_path)
);

ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_documents
    USING (tenant_id = current_setting('app.current_tenant')::uuid);

CREATE INDEX kb_documents_tenant_kb_idx ON kb_documents (tenant_id, kb_id);
```

### 6.2 `kb_chunks`

```sql
CREATE TABLE kb_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,                   -- denorm pra RLS + filter speed
    kb_id           TEXT NOT NULL,                   -- denorm pra filter sem JOIN
    chunk_idx       INT  NOT NULL,
    heading_path    TEXT,
    content         TEXT NOT NULL,
    token_count     INT  NOT NULL,
    embedding       vector(1536) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (document_id, chunk_idx)
);

ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_chunks
    USING (tenant_id = current_setting('app.current_tenant')::uuid);

CREATE INDEX kb_chunks_filter_idx ON kb_chunks (tenant_id, kb_id);
CREATE INDEX kb_chunks_embedding_idx ON kb_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### 6.3 Migration `0005_kb_tables.py`

- `CREATE EXTENSION IF NOT EXISTS vector;` (idempotente)
- DDL acima
- Sem dados de seed (CLI indexer popula)

---

## 7. Pipelines (fluxos de execução)

### 7.1 KB indexing

```
   kb/<tenant>/<kb_id>/*.md
            │
            ▼
   ┌────────────────────────────────────┐
   │ for each .md file under kb_root/   │
   │   <tenant.slug>/[<kb_id>/]:        │
   └─────────┬──────────────────────────┘
             ▼
     content = read_file()
     digest = sha256(content)
             │
             ▼
     ┌──────────────────────────────────┐
     │ SELECT kb_documents              │
     │ WHERE tenant_id = ?              │
     │   AND kb_id = ?                  │
     │   AND doc_path = ?               │
     └───────┬──────────────────────────┘
             │
       ┌─────┴─────┐
       │ same hash?│
       └─────┬─────┘
   yes ─────┘  └───── no (or missing)
    │                  │
    ▼                  ▼
  skip            DELETE kb_chunks WHERE document_id = ?
                       │
                       ▼
              chunker.split(content) → list[ChunkDraft]
                       │
                       ▼
              embedder.embed_documents([c.content]) → list[vector]
                       │
                       ▼
              UPSERT kb_documents (content_md, content_hash, indexed_at=now())
              INSERT kb_chunks (drafts + embeddings)
                       │
                       ▼
              log "kb.indexed" {kb_id, doc_path, chunks, took_ms, cost_usd_estimate}

   se --prune:
     diff(FS docs, DB rows) → DELETE kb_documents WHERE doc_path NOT IN (...)
     log "kb.pruned" {kb_id, doc_path}
```

### 7.2 Retrieval (por turn)

```
   user_input = state["last_user_input"]
   node.knowledge_base = [KBRef(id=..., top_k=3, min_score=0.7)]
            │
            ▼
   embedder.embed_query(user_input) → list[float] (1536d)
            │
            ▼
   ┌─────────────────────────────────────────────────────────┐
   │  SELECT id, content, heading_path, kb_id,               │
   │         1 - (embedding <=> $query_vec) AS score         │
   │  FROM kb_chunks                                         │
   │  WHERE tenant_id = $tenant                              │
   │    AND kb_id = ANY($kb_ids)                             │
   │  ORDER BY embedding <=> $query_vec ASC                  │
   │  LIMIT $top_k_max                                       │
   └──────────────────────────┬──────────────────────────────┘
                              ▼
            filter Python: score >= min_score (do KBRef do chunk.kb_id)
                              ▼
                  list[RetrievedChunk]
                              ▼
              render como bloco:
              "<knowledge_base>
              [1] Preços > Mentoria (score 0.84)
              <conteúdo do chunk>

              [2] ...
              </knowledge_base>"
                              ▼
              log "kb.retrieved" {node, chunks_count, top_score}
              (se vazio: log "kb.no_match")
```

### 7.3 Guardrails (post-LLM)

Ver pseudocódigo em §4.7 (`run_with_guardrails`).

Visualmente:

```
   build base_messages (system + KB + history + user_input)
            ▼
   ┌─────────────────────────────────────────────────────────┐
   │ run_with_guardrails(inner=_invoke_node_llm, ...)        │
   └─────────┬───────────────────────────────────────────────┘
             ▼
   attempt 0 → inner(messages) → result
             ▼
   validate_whitelist(result, guardrails) → verdict
             ▼
       ┌─────┴─────┐
       │ passed?   │
       └─────┬─────┘
       yes ──┘  └── no → attempt += 1, append SystemMessage(suggested_fix), loop
        │
        ▼
   if node.critical and guardrails.critic_enabled:
     critic_pass(result, kb_chunks, history, guardrails) → verdict
       ┌─────┴─────┐
       │ passed?   │
       └─────┬─────┘
       yes ──┘  └── no → attempt += 1, append SystemMessage, loop
        │
        ▼
   return GuardrailsRunResult(response, collected, blocked=False, attempts)

   se exhausted (attempt > max_retries):
     _handle_exhausted(...) → response_text=fallback, blocked=True
     log "guardrail.fallback_used" {last_reason}
```

---

## 8. Caching strategy (Anthropic prompt caching)

### 8.1 Estrutura final da chamada LLM

```python
# Anthropic, cache_enabled=true
messages = [
    SystemMessage(content=[
        {"type": "text",
         "text": f"{node.prompt}\n\n{tenant_context}",     # estático
         "cache_control": {"type": "ephemeral"}},
        {"type": "text",
         "text": f"<knowledge_base>\n{kb_block}\n</knowledge_base>"},  # dinâmico
    ]),
    *history,                          # cresce por turn, não cacheado
    HumanMessage(user_input),
]
```

- **Bloco cacheado:** `node.prompt + tenant_context` (estável por TreeFlow version + tenant). Tools (structured model com `prices_mentioned`/`products_mentioned`) ficam no prefixo cacheado automaticamente.
- **Não cacheado:** KB chunks (mudam por turn), history (cresce), user_input (cresce).
- **TTL:** 5 minutos (ephemeral). Anthropic auto-refresh enquanto há hits.
- **Pricing impacto:** cache write 1.25×, cache read 0.1×. Break-even: 1 hit já paga o write extra.
- **Mínimo cacheável:** ~1024 tokens. Abaixo disso, `cache_control` ignorado pelo provider (sem erro, sem benefício).

### 8.2 TreeFlow loader warning

Em `TreeFlowLoader.load()`, pós-validação:

```python
if tenant.llm.cache_enabled:
    for node in tf.nodes:
        approximate_tokens = count_tokens(node.prompt)
        if approximate_tokens < 1024:
            log.warning("treeflow.cache_below_threshold",
                       tenant=tenant.slug, treeflow=tf.id, node=node.id,
                       prompt_tokens=approximate_tokens, threshold=1024)
```

Não bloqueia load. Só sinaliza.

### 8.3 OpenAI

OpenAI tem prompt caching automático pra prefixos ≥1024 tokens desde out/2024, sem `cache_control` explícito. `build_system_messages(provider="openai")` concatena tudo num único `SystemMessage(content=str)` pra maximizar o prefixo cacheável automaticamente.

### 8.4 Toggle (`cache_enabled`)

`tenant.llm.cache_enabled: bool` (default `true`). Comportamento:

- **Anthropic + `true`:** `build_system_messages` aplica `cache_control={"type": "ephemeral"}` no bloco estático. Cache ativo.
- **Anthropic + `false`:** `build_system_messages` omite `cache_control`. Sem cache.
- **OpenAI (qualquer valor):** OpenAI faz auto-caching de prefixos ≥1024 tok sem marker; **não há API pra desligar**. Toggle é efetivamente no-op pra tenants OpenAI. Spec documenta limitação; loader pode logar `treeflow.cache_toggle_noop` se tenant tiver `provider=openai` + `cache_enabled=false` (sinaliza incoerência).

**Use caso pra `false`:** dev de tenant Anthropic quer medir cost real sem cache (cost analytics baseline). Pra OpenAI, recomenda-se simplesmente não mexer no toggle (deixar default `true`).

### 8.5 Non-goals de caching no Plano 3

- 1-hora extended TTL.
- Cache de history (turnos passados estáveis).
- Cache de KB chunks (impossível — dinâmico).
- Cache hit rate metrics (Plano de observabilidade futuro).

---

## 9. Error handling — política por classe

| Situação | Política | Onde |
|---|---|---|
| Embed query OpenAI falha (network/rate-limit) | Log `kb.embed_error`, segue **sem** KB block (não bloqueia turn) | `retriever.py` |
| Postgres retrieval falha | Propaga exception — runtime lida no nível acima | `retriever.py` |
| `node.knowledge_base[*].id` não existe em `kb_documents` | Log warning, retorna `[]` | `retriever.py` |
| Nenhum chunk passa `min_score` | Log info `kb.no_match`, segue sem KB block | `retriever.py` |
| LLM extract falha (provider error) | Propaga — Plano 8 trata retry; aqui é fail-fast | `compiler.py` |
| LLM emite `prices_mentioned` com float em vez de int | Pydantic rejeita → conta como retry attempt | `extractor.py` |
| Whitelist falha 3x (max_retries + 1) | `_handle_exhausted` → fallback genérico, blocked=True | `runner.py` |
| Critic falha 3x | Idem | `runner.py` |
| `tenant.guardrails.enabled=false` | No-op em validate_whitelist e critic_pass | `runner.py` |
| `node.critical=true` mas `guardrails.critic_enabled=false` | Skip critic_pass (config tenant trumps node) | `runner.py` |
| Indexer encontra `.md` inválido (não-UTF8, encoding error) | Skip arquivo, adiciona a `IndexResult.failed`, continua resto | `indexer.py` |
| Indexer com `--prune` deletaria doc referenciado por talkflow ativo? | Não há FK cruzada; chunks não são referenciados após retrieval. Safe. | n/a |
| `prompt < 1024 tok` com `cache_enabled=true` | Log warning, segue (cache silencioso ignorado pelo provider) | `loader.py` |
| OpenAI embedding key ausente em secrets | Indexer e retriever falham com erro claro no startup | `embeddings.py` |

---

## 10. Telemetria

### 10.1 Novos eventos structlog

| Event | Campos | Quando |
|---|---|---|
| `kb.indexed` | `tenant`, `kb_id`, `doc_path`, `chunks`, `took_ms`, `cost_usd_estimate` | Por doc indexado |
| `kb.skipped` | `tenant`, `kb_id`, `doc_path`, `reason="hash_unchanged"` | Por doc skippado |
| `kb.pruned` | `tenant`, `kb_id`, `doc_path` | Quando `--prune` deleta |
| `kb.retrieved` | `talkflow_id`, `node`, `query_preview`, `chunks` (list[{kb_id, score, heading}]), `top_score` | Por retrieval |
| `kb.no_match` | `talkflow_id`, `node`, `query_preview`, `min_score`, `kb_ids` | Quando nenhum chunk passa filtro |
| `kb.embed_error` | `tenant`, `error` | Quando embed call falha |
| `guardrail.blocked` | `talkflow_id`, `node`, `attempt`, `reason`, `suggested_fix` | Por whitelist fail |
| `guardrail.fallback_used` | `talkflow_id`, `node`, `last_reason` | Quando max_retries exhausted |
| `critic.flagged` | `talkflow_id`, `node`, `attempt`, `reason`, `suggested_fix` | Por critic fail |
| `critic.fallback_used` | `talkflow_id`, `node`, `last_reason` | Quando critic exhausted |
| `treeflow.cache_below_threshold` | `tenant`, `treeflow`, `node`, `prompt_tokens`, `threshold` | Loader detecta prompt curto |

Cost estimates calculados em-line (preço por token × token count). Pra Plano 3, log direto (Plano de observabilidade depois agrega métricas).

---

## 11. Testing strategy

### 11.1 Unit (`tests/unit/`)

- **`test_kb_chunker.py`**: markdown com `##` simples; aninhado `### ####`; sem headers; com cap forçando split por parágrafo; cap forçando split por sentence; UTF-8 com emoji; arquivo vazio; heading sem corpo.
- **`test_kb_yaml_schema.py`**: KBRef defaults; KBRef `top_k=0` rejeitado; `min_score=1.5` rejeitado; GuardrailsConfig `enabled=true` sem `allowed_prices`/`allowed_products` rejeitado; NodeSpec.knowledge_base com KBRef válido; com forward-compat `dict` antigo rejeitado.
- **`test_whitelist_validator.py`**: pass (todos mencionados ok); fail price (R$ fora); fail product (string fora, case-insensitive); empty mentioned (passa); `enabled=false` (no-op).
- **`test_guardrails_runner.py`** (FakeListChatModel): passa na 1ª; retry 1x e passa; retry 2x e passa; exausta retries e fallback; critic ligado e passa; critic ligado e falha; critic_enabled=false ignora node.critical=true.
- **`test_messages_builder.py`**: provider=anthropic + cache=true → content list com cache_control; cache=false → sem cache_control; provider=openai → string concatenada.

### 11.2 Integration (`tests/integration/`)

Requer `make up` (docker compose com Postgres + pgvector).

- **`test_kb_indexer.py`**: escreve `.md` em tmp_path, roda `reindex_tenant_kb`, verifica `kb_documents` + `kb_chunks` populated; muda conteúdo, re-roda, verifica idempotência via content_hash; deleta `.md` + `--prune=True`, verifica rows removidas; multi-doc batch.
- **`test_kb_retriever.py`**: popula chunks via indexer (com FakeEmbedder pré-determinístico), embed query (também fake), verifica top-k order + min_score filter + RLS isolation (tenant A não vê chunks de tenant B).
- **`test_kb_indexer_cli.py`**: invoca `ai-sdr reindex-kb --tenant example` via subprocess, valida DB state.
- **`test_compiler_with_kb_and_guardrails.py`**: TreeFlow com node tendo KB + critic=false; FakeListChatModel scripted: response 1 com preço inválido → retry → response 2 válida; valida state final + attempts==1; segundo cenário: 3 responses inválidas → fallback acionado, blocked=True.
- **`test_guardrails_critic.py`**: critic_pass com FakeListChatModel scripted retornando Verdict; valida que critic recebe os campos certos (kb_chunks rendered, guardrails as YAML).

### 11.3 Live LLM (`@pytest.mark.live_llm`, skip por default)

- **`test_kb_live.py`**: requer `OPENAI_API_KEY` em env. Indexa 1 doc real (`tests/fixtures/kb_test.md`), embed query óbvia ("qual o preço?"), verifica que chunk relevante volta com score > 0.5.

### 11.4 Markers em `pyproject.toml`

Já existe `live_llm` (Plano 2). Não adiciona marker novo — testes de KB vão em `integration` padrão (rodam com `make test-integration`).

---

## 12. Custos estimados (sanity check)

**Indexing (one-shot por tenant):**
- KB do piloto: ~5 KBs × 5 docs cada × ~2000 tok/doc = ~50k tokens total
- Embedding: 50k × $0.02/M = **$0.001** total. Negligível.

**Per-turn (steady state, critic OFF):**
- Embed query: ~50 tok × $0.02/M = $0.000001
- LLM main: ~3000 tok input (1500 cached after T1, 1500 fresh) + 200 out
  - Cache hit (T2+): 1500 × 0.1× + 1500 × 1× + 200 × 5× (output rate) = ~$0.008
  - First turn no cache: 3000 × 1× + 200 × 5× = ~$0.012
- **Steady state: ~$0.008/turn**

**Per-turn worst case (critic ON, 2 retries):**
- 3× LLM main ~ $0.024
- 3× critic Haiku (~2000 tok input + 200 out × $0.001/$0.005 per M) ~ $0.003
- **Worst case: ~$0.027/turn**

Pra piloto com ~1000 turns/mês: $8-30/mês LLM. Bem dentro do `tenant.limits.max_usd_per_day: 50` configurado.

---

## 13. Decisões abertas (a refinar na fase de plano)

- **Versão min de `langchain-anthropic`:** suporte a `cache_control` em content blocks via SystemMessage com content=list[dict] requer `langchain-anthropic >= 0.3`. Plano deve travar versão exata no `pyproject.toml` e validar via teste unit que `build_system_messages(provider="anthropic")` produz o formato esperado.
- **Estratégia exata de `count_tokens` em `loader.py`:** usar `tiktoken` com encoding `cl100k_base` (alinhado com `text-embedding-3-small`) ou Anthropic-specific (`anthropic.count_tokens` se exposta no SDK). `cl100k_base` é safe approximation pro warning de cache threshold — não precisa ser exato.
- **Top-k agregado vs por-KB:** quando node tem 2+ KBRefs com `top_k` diferente, retriever usa `max()` (simplificação MVP). Pode causar over-retrieval pra KBs com `top_k` baixo. Re-examinar se algum tenant configurar 5+ KBs no mesmo node.
- **Pricing exato de cache write/read pode mudar** — plano deve verificar pricing atual da Anthropic na hora da implementação de telemetria de custo (cost_usd_estimate em `kb.indexed` e `llm.called`).
- **Heading_path renderização:** hoje "Preços > Mentoria" como string simples. Avaliar se LLM se beneficia de formato diferente (e.g. `[Section: Preços > Mentoria]` em vez de breadcrumb cru). Decisão tomada no plano via prompt-engineering test.
- **Snapshot `content_md` em `kb_documents`:** duplica conteúdo do FS. Útil pra audit + re-chunk sem re-ler FS. Custo: ~5KB por doc × poucos docs = irrelevante. Manter por enquanto; remover se KB crescer muito.

---

## 14. Glossário (delta)

- **Chunk** — fatia de um documento `.md`, indexada com embedding. Unit de retrieval.
- **KBRef** — referência a uma KB declarada num node do TreeFlow (`{id, top_k, min_score}`).
- **RetrievedChunk** — chunk recuperado pelo retriever com score de similaridade.
- **Verdict** — output dos validators (whitelist, critic): `{passed, reason, suggested_fix}`.
- **Critic pass** — segundo LLM (Haiku) que revisa response antes de enviar. Roda se `node.critical=true` e `tenant.guardrails.critic_enabled=true`.
- **Whitelist** — listas allowlist em `tenant.guardrails.allowed_prices/allowed_products`.
- **Fallback** — texto genérico do tenant emitido quando retries esgotam.
- **HITL** — Human-in-the-loop. Substituição futura do fallback genérico por workflow de aprovação humana.

---

**Fim do spec.**
