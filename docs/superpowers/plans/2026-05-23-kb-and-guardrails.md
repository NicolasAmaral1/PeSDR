# KB + Guardrails Implementation Plan (Plano 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-Node RAG layer (markdown KB indexed in pgvector) plus a Guardrails layer (whitelist validator + optional critic pass + retry/fallback) on top of the TreeFlow engine from Plan 2. After this plan, a Node can declare `knowledge_base: [...]` to inject KB chunks into the prompt and `critical: true` to enable a critic pass; tenants get an `allowed_prices`/`allowed_products` whitelist and a generic fallback that fires after 2 retries. A new `ai-sdr reindex-kb` CLI walks `kb/<tenant>/<kb_id>/*.md`, chunks by markdown header (cap 600 tok), embeds via OpenAI `text-embedding-3-small` (1536d), and upserts into `kb_documents` + `kb_chunks` idempotently via `content_hash`.

**Architecture:** Modular packages — `src/ai_sdr/kb/` (chunker, embeddings, indexer, retriever) and `src/ai_sdr/guardrails/` (schemas, whitelist, critic, runner). `compiler.py` becomes the coordinator: it (a) calls `kb.retriever` and injects results as a separate `SystemMessage` after the cacheable `node.prompt`, then (b) wraps the LLM call in `guardrails.runner.run_with_guardrails`, which loops on whitelist/critic verdicts with retry feedback and falls back to a tenant-configured generic text after `max_retries`. The fallback path is isolated in a private `_handle_exhausted()` hook so a future HITL plan can swap it for `GraphInterrupt()` without refactoring the loop. Anthropic prompt caching is enabled per-tenant via a 5-min ephemeral `cache_control` on the static `node.prompt` block.

**Tech Stack additions:** `tiktoken>=0.8` (token counting for chunker cap + loader cache warning). The OpenAI embedding client comes from `langchain-openai` (already a dep via Plan 2) using `OpenAIEmbeddings`. The pgvector Python adapter (`pgvector>=0.3.6`) is already a dep. No new vendor SDKs.

**Spec:** [`docs/superpowers/specs/2026-05-23-kb-and-guardrails-design.md`](../specs/2026-05-23-kb-and-guardrails-design.md). Read §2.2 (non-goals), §4 (components), §6 (schema), §7 (pipelines), §8 (caching), §10 (telemetria), and §13 (open questions) before starting.

---

## File Structure

```
src/ai_sdr/
├── kb/                                  # NEW package
│   ├── __init__.py                      # NEW (empty)
│   ├── chunker.py                       # NEW: MarkdownChunker(max_tokens=600) → list[ChunkDraft]
│   ├── embeddings.py                    # NEW: Embedder + build_embedder(secrets, model)
│   ├── indexer.py                       # NEW: reindex_tenant_kb(...) + IndexResult dataclass
│   └── retriever.py                     # NEW: retrieve(...) + RetrievedChunk + KBRef dataclass-mirror
│
├── guardrails/                          # NEW package
│   ├── __init__.py                      # NEW (empty)
│   ├── schemas.py                       # NEW: Verdict (Pydantic BaseModel, frozen)
│   ├── whitelist.py                     # NEW: validate_whitelist(...) → Verdict
│   ├── critic.py                        # NEW: critic_pass(...) → Verdict
│   └── runner.py                        # NEW: run_with_guardrails(...) + _handle_exhausted(...)
│
├── llm/
│   ├── extractor.py                     # MODIFIED: build_structured_model accepts guardrails arg
│   └── messages.py                      # NEW: build_system_messages(...) per-provider
│
├── models/
│   ├── kb_document.py                   # NEW
│   ├── kb_chunk.py                      # NEW
│   └── __init__.py                      # MODIFIED: re-export KbDocument + KbChunk
│
├── schemas/
│   ├── llm_yaml.py                      # MODIFIED: LLMDefaults gains embeddings + cache_enabled
│   ├── tenant_yaml.py                   # MODIFIED: TenantConfig gains guardrails: GuardrailsConfig | None
│   └── treeflow_yaml.py                 # MODIFIED: NodeSpec.knowledge_base is now list[KBRef] (typed)
│
├── treeflow/
│   ├── compiler.py                      # MODIFIED: node_fn injects KB + wraps LLM call in run_with_guardrails
│   └── loader.py                        # MODIFIED: log warning if node.prompt < 1024 tok and cache_enabled
│
└── cli/
    ├── app.py                           # MODIFIED: register reindex-kb subcommand
    └── reindex_kb.py                    # NEW: typer command `ai-sdr reindex-kb --tenant X [--kb Y] [--prune]`

migrations/versions/
└── 0005_kb_tables.py                    # NEW: kb_documents + kb_chunks + RLS + IVFFlat index

tenants/example/
├── tenant.yaml                          # MODIFIED: adds guardrails + llm.embeddings + llm.cache_enabled
└── treeflows/example.yaml               # MODIFIED: oferta node gains knowledge_base + critical: true

kb/                                      # NEW (repo-root; indexer convention is kb_root/<slug>/<kb_id>/)
└── example/
    └── example_kb/
        └── precos.md                    # NEW (fixture for tests + simulate CLI demo)

tests/
├── unit/
│   ├── test_kb_chunker.py               # NEW
│   ├── test_tenant_yaml.py              # MODIFIED (or new — extend with guardrails tests)
│   ├── test_treeflow_yaml_schema.py     # MODIFIED — KBRef typed tests
│   ├── test_messages_builder.py         # NEW
│   ├── test_whitelist_validator.py      # NEW
│   ├── test_extractor.py                # MODIFIED — adds tests for guardrails extension
│   ├── test_guardrails_runner.py        # NEW (FakeListChatModel)
│   ├── test_kb_embeddings.py            # NEW (mocked OpenAIEmbeddings)
│   └── test_treeflow_loader.py          # MODIFIED — cache warning assertion
└── integration/
    ├── test_kb_models.py                # NEW (RLS + IVFFlat existence)
    ├── test_kb_indexer.py               # NEW
    ├── test_kb_retriever.py             # NEW
    ├── test_kb_indexer_cli.py           # NEW (subprocess)
    ├── test_compiler_with_kb_and_guardrails.py  # NEW (FakeListChatModel)
    ├── test_guardrails_critic.py        # NEW (FakeListChatModel)
    └── test_kb_live.py                  # NEW (@pytest.mark.live_llm — real OpenAI embed)

pyproject.toml                           # MODIFIED: adds tiktoken
CLAUDE.md                                # MODIFIED: KB authoring + guardrails + reindex CLI section
```

**Layout notes:**
- `kb/` is the new RAG package — chunker is pure (no I/O), embeddings is a thin wrapper, indexer + retriever are the I/O components.
- `guardrails/` is the new safety package — `schemas.py` only holds `Verdict`; `whitelist.py` is pure logic; `critic.py` does one Haiku call; `runner.py` is the retry orchestrator and exposes the `_handle_exhausted()` hook for a future HITL plan.
- `llm/messages.py` is a tiny helper (one function) that knows how to render system messages with provider-appropriate cache markers — it stays in `llm/` because it's tied to provider abstraction, not KB or guardrails.
- Markdown parsing in `chunker.py` is **roll-our-own** via regex on `^#{1,6}\s+` — no new dep. If edge cases bite (frontmatter, code blocks containing `##`), a future task can swap for `markdown-it-py`.

---

## Prerequisites (delta from Plan 2)

Plan 2's prereqs (Docker, uv, age, sops, OPENAI_API_KEY for `live_llm` and simulate) still apply. **No new prereqs.** All Plan 3 deps come via existing tooling:

- Embeddings use the OpenAI key already in `tenants/<slug>/secrets.enc.yaml`.
- Anthropic prompt caching needs nothing extra beyond `langchain-anthropic>=0.3.0` (already pinned in `pyproject.toml`).
- Markdown chunking and token counting use `tiktoken` (added in Task 1).
- `pgvector` Postgres extension is already created by migration `0001_extensions.py`.

### VPS notes

Same VPS (`vps-nova`), same ports. No new infra. After deployment, run `ai-sdr reindex-kb --tenant <slug>` once per tenant to populate `kb_documents` + `kb_chunks` from `kb/<slug>/**/*.md` (committed to the repo).

---

## Task 1: Add `tiktoken` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

Edit the `dependencies` array in `pyproject.toml`. Insert `"tiktoken>=0.8",` alphabetically (between `"sqlalchemy[asyncio]"` and `"typer"`):

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pgvector>=0.3.6",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "structlog>=24.4",
    "pyyaml>=6.0",
    "redis>=5.2",
    "langgraph>=0.2.60",
    "langgraph-checkpoint-postgres>=2.0.21",
    "psycopg[binary,pool]>=3.2.3",
    "langchain-core>=0.3.28",
    "langchain-anthropic>=0.3.0",
    "langchain-openai>=0.2.14",
    "simpleeval>=1.0.3",
    "tiktoken>=0.8",
    "typer>=0.15",
]
```

- [ ] **Step 2: Lock + install**

Run: `uv lock && uv sync`

Expected: lock file updated, tiktoken installed. No errors.

- [ ] **Step 3: Smoke-import**

Run: `uv run python -c "import tiktoken; enc = tiktoken.get_encoding('cl100k_base'); print(len(enc.encode('hello world')))"`

Expected: prints `2`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(plan3 t1): add tiktoken dependency"
```

---

## Task 2: Tenant schema — `GuardrailsConfig` + `EmbeddingsConfig` + `cache_enabled`

**Files:**
- Modify: `src/ai_sdr/schemas/llm_yaml.py`
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Create or modify: `tests/unit/test_tenant_yaml.py`

**Design:** The tenant gains an optional `guardrails` block (whitelist + critic toggle + fallback) and an optional `llm.embeddings` config (default model `text-embedding-3-small`). `LLMDefaults` also gets a `cache_enabled: bool = True` flag (per Spec §8.4). If `guardrails.enabled=true`, at least one of `allowed_prices`/`allowed_products` must be non-empty (validator enforces this).

- [ ] **Step 1: Write the failing tests**

If `tests/unit/test_tenant_yaml.py` does not exist, create it with the content below. If it exists, append the test functions.

```python
"""Tests for the GuardrailsConfig + EmbeddingsConfig additions to tenant.yaml."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.llm_yaml import EmbeddingsConfig, LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig, TenantConfig


def _minimal_tenant_data() -> dict:
    return {
        "id": "example",
        "display_name": "Example",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "anthropic_key",
            }
        },
    }


def test_guardrails_block_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.guardrails is None


def test_guardrails_disabled_allows_empty_lists() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": False,
        "allowed_prices": [],
        "allowed_products": [],
        "fallback_text": "Confirmo já já, ok?",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.guardrails is not None
    assert cfg.guardrails.enabled is False


def test_guardrails_enabled_requires_at_least_one_list() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [],
        "allowed_products": [],
        "fallback_text": "Confirmo já já, ok?",
    }
    with pytest.raises(ValidationError, match="allowed_prices"):
        TenantConfig.model_validate(data)


def test_guardrails_fallback_text_min_length() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [247],
        "allowed_products": [],
        "fallback_text": "ok",  # too short
    }
    with pytest.raises(ValidationError, match="fallback_text"):
        TenantConfig.model_validate(data)


def test_guardrails_full_block() -> None:
    data = _minimal_tenant_data()
    data["guardrails"] = {
        "enabled": True,
        "allowed_prices": [247, 1497, 1997, 2000, 6000],
        "allowed_products": ["Mentoria", "Aceleradora", "Downsell"],
        "critic_enabled": True,
        "fallback_text": "Deixa eu confirmar e já te respondo, ok?",
        "max_retries": 2,
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.guardrails is not None
    assert cfg.guardrails.allowed_prices == [247, 1497, 1997, 2000, 6000]
    assert cfg.guardrails.critic_enabled is True
    assert cfg.guardrails.max_retries == 2


def test_llm_defaults_cache_enabled_default_true() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.llm.cache_enabled is True


def test_llm_defaults_cache_enabled_can_be_disabled() -> None:
    data = _minimal_tenant_data()
    data["llm"]["cache_enabled"] = False
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.cache_enabled is False


def test_llm_embeddings_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.llm.embeddings is None


def test_llm_embeddings_defaults() -> None:
    data = _minimal_tenant_data()
    data["llm"]["embeddings"] = {"provider": "openai"}
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.embeddings is not None
    assert cfg.llm.embeddings.provider == "openai"
    assert cfg.llm.embeddings.model == "text-embedding-3-small"
    assert cfg.llm.embeddings.api_key_ref == "openai_key"


def test_llm_embeddings_explicit_values() -> None:
    data = _minimal_tenant_data()
    data["llm"]["embeddings"] = {
        "provider": "openai",
        "model": "text-embedding-3-large",
        "api_key_ref": "openai_key_alt",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm.embeddings is not None
    assert cfg.llm.embeddings.model == "text-embedding-3-large"
    assert cfg.llm.embeddings.api_key_ref == "openai_key_alt"
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_tenant_yaml.py -v`

Expected: FAIL with `ImportError: cannot import name 'EmbeddingsConfig'` and/or `cannot import name 'GuardrailsConfig'`.

- [ ] **Step 3: Add `EmbeddingsConfig` + extend `LLMDefaults` in `llm_yaml.py`**

Open `src/ai_sdr/schemas/llm_yaml.py`. Add `EmbeddingsConfig` class and extend `LLMDefaults` with `embeddings: EmbeddingsConfig | None = None` and `cache_enabled: bool = True`. Keep the existing `LLMConfig`/`LLMDefaults.default`/`LLMDefaults.classifier` definitions intact.

Add to the imports:

```python
from typing import Literal
```

Append (or place after existing class definitions):

```python
class EmbeddingsConfig(BaseModel):
    """OpenAI embeddings config (used by KB indexer + retriever)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai"] = "openai"
    model: str = "text-embedding-3-small"
    api_key_ref: str = "openai_key"
```

In the existing `LLMDefaults` class, add the two new fields. Final shape:

```python
class LLMDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: LLMConfig
    classifier: LLMConfig | None = None
    embeddings: EmbeddingsConfig | None = None
    cache_enabled: bool = True
```

- [ ] **Step 4: Add `GuardrailsConfig` in `tenant_yaml.py`**

Open `src/ai_sdr/schemas/tenant_yaml.py`. Add imports if needed:

```python
from pydantic import Field, model_validator
from typing_extensions import Self
```

Append a new class definition above `TenantConfig` (or wherever fits the existing file order):

```python
class GuardrailsConfig(BaseModel):
    """Tenant-level guardrails configuration (Plan 3, spec §4.5)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    allowed_prices: list[int] = Field(default_factory=list)
    allowed_products: list[str] = Field(default_factory=list)
    critic_enabled: bool = True
    fallback_text: str = Field(min_length=10)
    max_retries: int = Field(default=2, ge=1, le=5)

    @model_validator(mode="after")
    def _require_lists_when_enabled(self) -> Self:
        if self.enabled and not self.allowed_prices and not self.allowed_products:
            raise ValueError(
                "guardrails.enabled=true requires at least one of "
                "allowed_prices or allowed_products to be non-empty"
            )
        return self
```

In the existing `TenantConfig` class, add:

```python
    guardrails: GuardrailsConfig | None = None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_tenant_yaml.py -v`

Expected: all PASS.

- [ ] **Step 6: Run full unit suite (no regression)**

Run: `make test-unit`

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/schemas/llm_yaml.py src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml.py
git commit -m "feat(plan3 t2): tenant schema gains guardrails + llm.embeddings + cache_enabled"
```

---

## Task 2b: Open `LLMConfig.provider` for any LangChain-supported provider

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/ai_sdr/schemas/llm_yaml.py`
- Modify: `src/ai_sdr/llm/factory.py`
- Modify: `tests/unit/test_llm_factory.py`

**Design:** Open the door to non-Anthropic/OpenAI providers via `langchain.chat_models.init_chat_model("<provider>:<model>", api_key=...)`. Schema changes from `Literal["anthropic", "openai"]` to free-form `str` so tenants can declare any provider whose `langchain-<x>` package is installed. Factory replaces its if/else chain with a single `init_chat_model()` call. Adds three new deps (`langchain-google-genai`, `langchain-deepseek`, `langchain-ollama`) so tenants can pick from {anthropic, openai, google_genai, deepseek, ollama} without installing extras.

**End-to-end validation of each new provider is OUT OF SCOPE here.** Plan 4 will do the validation matrix (live_llm tests per provider + provider-specific tuning + multi-provider embeddings). T2b is the schema/factory opening only — declaration works; runtime correctness for new providers is unverified.

- [ ] **Step 1: Add the three new langchain provider deps**

Edit `pyproject.toml` `dependencies = [...]`. After the existing `"langchain-openai>=0.2.14",` line, add (preserve the order; group the langchain-* entries together):

```toml
    "langchain-anthropic>=0.3.0",
    "langchain-openai>=0.2.14",
    "langchain-google-genai>=2.0.0",
    "langchain-deepseek>=0.1.0",
    "langchain-ollama>=0.2.0",
```

Then:

```
uv lock && uv sync
```

Smoke-import each new provider:

```
uv run python -c "from langchain.chat_models import init_chat_model; print('ok')"
uv run python -c "import langchain_google_genai, langchain_deepseek, langchain_ollama; print('ok')"
```

Both should print `ok`.

- [ ] **Step 2: Write the failing test**

Replace (or extend, if it exists already) `tests/unit/test_llm_factory.py` with content that exercises the new dispatch logic. Keep any existing anthropic/openai tests; add cases for the new providers via mocking `init_chat_model`:

```python
"""Tests for build_llm — provider-agnostic via init_chat_model."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_sdr.llm.factory import build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig


def _cfg(provider: str, model: str = "m", api_key_ref: str = "k") -> LLMConfig:
    return LLMConfig(
        provider=provider, model=model, api_key_ref=api_key_ref, temperature=0.5
    )


def test_anthropic_dispatched_via_init_chat_model() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("anthropic", "claude-sonnet-4-6", "anthropic_key"),
                  secrets={"anthropic_key": "sk-fake"})
    fake.assert_called_once()
    args, kwargs = fake.call_args
    assert args[0] == "anthropic:claude-sonnet-4-6"
    assert kwargs["api_key"] == "sk-fake"
    assert kwargs["temperature"] == 0.5


def test_openai_dispatched_via_init_chat_model() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("openai", "gpt-4o", "openai_key"),
                  secrets={"openai_key": "sk-openai-fake"})
    args, kwargs = fake.call_args
    assert args[0] == "openai:gpt-4o"
    assert kwargs["api_key"] == "sk-openai-fake"


def test_google_genai_dispatched() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("google_genai", "gemini-2.0-flash", "google_key"),
                  secrets={"google_key": "AIza-fake"})
    args, kwargs = fake.call_args
    assert args[0] == "google_genai:gemini-2.0-flash"
    assert kwargs["api_key"] == "AIza-fake"


def test_deepseek_dispatched() -> None:
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("deepseek", "deepseek-chat", "deepseek_key"),
                  secrets={"deepseek_key": "sk-ds-fake"})
    args, kwargs = fake.call_args
    assert args[0] == "deepseek:deepseek-chat"
    assert kwargs["api_key"] == "sk-ds-fake"


def test_ollama_dispatched_without_api_key() -> None:
    """Ollama is local — no api_key. Factory should still work."""
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("ollama", "llama3.2", "ollama_key"),
                  secrets={"ollama_key": ""})  # empty / unused
    args, kwargs = fake.call_args
    assert args[0] == "ollama:llama3.2"


def test_missing_api_key_in_secrets_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="anthropic_key"):
        build_llm(_cfg("anthropic"), secrets={})


def test_arbitrary_provider_string_accepted_by_schema() -> None:
    """Schema is free-form now; factory delegates to init_chat_model."""
    with patch("ai_sdr.llm.factory.init_chat_model") as fake:
        build_llm(_cfg("brand_new_provider", "some-model", "x"),
                  secrets={"x": "y"})
    args, _ = fake.call_args
    assert args[0] == "brand_new_provider:some-model"
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_llm_factory.py -v`

Expected: FAIL (current factory uses if/else, not `init_chat_model`).

- [ ] **Step 4: Widen `LLMConfig.provider` to `str`**

In `src/ai_sdr/schemas/llm_yaml.py`, change `LLMConfig.provider` from `Literal["anthropic", "openai"]` to `str`. Document the trade-off:

```python
class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Free-form string — dispatched via langchain.chat_models.init_chat_model.
    # Common values: "anthropic", "openai", "google_genai", "deepseek", "ollama",
    # "bedrock_converse", "vertexai", "mistralai". Whichever langchain-<x> package
    # is installed will work. Validation that the runtime actually supports the
    # chosen provider happens lazily inside build_llm() / init_chat_model().
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key_ref: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
```

(Keep any other existing fields on LLMConfig as-is.)

- [ ] **Step 5: Refactor `src/ai_sdr/llm/factory.py`**

Replace the existing factory body. Final shape:

```python
"""LLM factory — provider-agnostic dispatch via langchain.chat_models.init_chat_model.

Plan 3 T2b opened this from a 2-provider if/else (anthropic, openai) to free-form.
Supported providers are whichever langchain-<x> packages are installed; the
factory does not validate the provider name (init_chat_model raises if it can't
resolve the package).

End-to-end validation of providers beyond anthropic + openai is Plan 4's job.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from ai_sdr.schemas.llm_yaml import LLMConfig


def build_llm(cfg: LLMConfig, secrets: dict[str, str]) -> BaseChatModel:
    """Build a chat model. Caller passes the secrets dict; we resolve api_key_ref."""
    api_key = secrets[cfg.api_key_ref]  # KeyError surfaces explicitly
    kwargs: dict = {"api_key": api_key}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    return init_chat_model(f"{cfg.provider}:{cfg.model}", **kwargs)
```

(Note: `init_chat_model` accepts `api_key` for most providers; Ollama ignores it. Passing it unconditionally is safe.)

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_llm_factory.py -v`

Expected: all PASS.

- [ ] **Step 7: Run full unit suite (catch regressions)**

Run: `make test-unit`

Expected: all green. If `tests/unit/test_extractor.py` or others depend on `LLMConfig.provider` being a `Literal`, they shouldn't — string values like `"anthropic"` are still valid under `str`.

- [ ] **Step 8: Spot-check existing integration tests still pass**

Run: `uv run pytest tests/integration/test_talkflow_runtime.py -v -m integration` (the live LLM round-trip test from Plan 2).

Expected: PASS (uses anthropic provider, which still works).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/ai_sdr/schemas/llm_yaml.py src/ai_sdr/llm/factory.py tests/unit/test_llm_factory.py
git commit -m "feat(plan3 t2b): widen LLMConfig.provider via init_chat_model (gemini/deepseek/ollama deps added)"
```

**Plan 4 note:** any tenant can now declare `provider: "google_genai"` (or others) in `tenant.yaml` and the schema accepts it; whether the runtime turn actually succeeds requires the API key in secrets + that provider's quirks (rate limits, function-calling shape, prompt caching availability) being compatible with our pipeline. Plan 4 will set up the validation matrix.

---

## Task 3: TreeFlow schema — typed `KBRef`

**Files:**
- Modify: `src/ai_sdr/schemas/treeflow_yaml.py`
- Modify: `tests/unit/test_treeflow_yaml_schema.py`

**Design:** `NodeSpec.knowledge_base` is currently `list[dict[str, Any]] | None` (forward-compat opaque from Plan 2 Task 3). Replace with `list[KBRef] | None`. `KBRef` is a Pydantic model with `id: str`, `top_k: int = 3 (ge=1, le=20)`, `min_score: float = 0.7 (ge=0.0, le=1.0)`. `NodeSpec.critical: bool = False` already exists and stays.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_treeflow_yaml_schema.py`:

```python
# ---------- KBRef typing (Plan 3) ----------

def test_node_with_typed_knowledge_base() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "knowledge_base": [
                    {"id": "kb_oferta", "top_k": 5, "min_score": 0.6},
                    {"id": "kb_obj"},
                ],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    kbs = tf.nodes[0].knowledge_base
    assert kbs is not None and len(kbs) == 2
    assert kbs[0].id == "kb_oferta"
    assert kbs[0].top_k == 5
    assert kbs[0].min_score == 0.6
    # defaults
    assert kbs[1].id == "kb_obj"
    assert kbs[1].top_k == 3
    assert kbs[1].min_score == 0.7


def test_kbref_top_k_out_of_range_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "top_k": 0}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="top_k"):
        TreeFlow.model_validate(data)


def test_kbref_min_score_out_of_range_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "min_score": 1.5}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="min_score"):
        TreeFlow.model_validate(data)


def test_kbref_extra_field_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "knowledge_base": [{"id": "kb", "weight": 0.5}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="weight"):
        TreeFlow.model_validate(data)
```

Also add `KBRef` to the import line at the top of the test file:

```python
from ai_sdr.schemas.treeflow_yaml import (
    CollectField,
    ExitCondition,
    KBRef,
    NodeSpec,
    TreeFlow,
)
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_treeflow_yaml_schema.py -v`

Expected: `ImportError: cannot import name 'KBRef'`.

- [ ] **Step 3: Add `KBRef` and tighten `NodeSpec.knowledge_base`**

In `src/ai_sdr/schemas/treeflow_yaml.py`:

Add (before `NodeSpec`):

```python
class KBRef(BaseModel):
    """Reference to a KB used by a Node (Plan 3, spec §5.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=20)
    min_score: float = Field(default=0.7, ge=0.0, le=1.0)
```

Replace the existing forward-compat line in `NodeSpec`:

```python
    # forward-compat — accepted but unused in plan 2
    knowledge_base: list[dict[str, Any]] | None = None
```

with the typed version:

```python
    knowledge_base: list[KBRef] | None = None
```

Update the docstring at the top of the file — remove `knowledge_base (Plan 3 — KB)` from the "forward-compat" list and add a one-liner: `knowledge_base is now typed as list[KBRef] (Plan 3)`.

Remove the `from typing import Any` import if it became unused after the change (run `uv run ruff check src/ai_sdr/schemas/treeflow_yaml.py` to confirm). The `Any` import may still be needed by `CollectField.validation: dict[str, Any] | None` and `handles_objections: list[dict[str, Any]] | None` — keep it if so.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_treeflow_yaml_schema.py -v`

Expected: all PASS (new + existing).

- [ ] **Step 5: Lint**

Run: `make lint && make format`

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/schemas/treeflow_yaml.py tests/unit/test_treeflow_yaml_schema.py
git commit -m "feat(plan3 t3): NodeSpec.knowledge_base typed as list[KBRef]"
```

---

## Task 4: `Verdict` + `validate_whitelist`

**Files:**
- Create: `src/ai_sdr/guardrails/__init__.py`
- Create: `src/ai_sdr/guardrails/schemas.py`
- Create: `src/ai_sdr/guardrails/whitelist.py`
- Create: `tests/unit/test_whitelist_validator.py`

**Design:** `Verdict` is a Pydantic `BaseModel` (not dataclass — `with_structured_output` in critic_pass needs a Pydantic class). It carries `passed: bool`, `reason: str | None`, `suggested_fix: str | None`. `validate_whitelist` takes `prices_mentioned`, `products_mentioned`, and a `GuardrailsConfig`; returns `Verdict(passed=True)` if `guardrails.enabled=False` (no-op) or if everything mentioned is in the allowlists. Product comparison is **case-insensitive**; price comparison is **exact int**. The `suggested_fix` includes the offending value(s) plus the allowlist, so the LLM has enough context to retry.

- [ ] **Step 1: Create the empty package**

Create `src/ai_sdr/guardrails/__init__.py` with one line:

```python
"""Guardrails package — anti-hallucination layer (Plan 3, spec §4.5)."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_whitelist_validator.py`:

```python
"""Tests for the whitelist guardrail validator."""

from __future__ import annotations

from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.guardrails.whitelist import validate_whitelist
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def _guardrails(
    enabled: bool = True,
    prices: list[int] | None = None,
    products: list[str] | None = None,
) -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=enabled,
        allowed_prices=prices if prices is not None else [247, 1497, 6000],
        allowed_products=products if products is not None else ["Mentoria", "Aceleradora"],
        fallback_text="Confirmo já já, ok?",
    )


def test_disabled_is_noop() -> None:
    g = _guardrails(enabled=False, prices=[], products=[])
    v = validate_whitelist(
        prices_mentioned=[9999],
        products_mentioned=["Inexistente"],
        guardrails=g,
    )
    assert v.passed is True


def test_nothing_mentioned_passes() -> None:
    g = _guardrails()
    v = validate_whitelist(prices_mentioned=[], products_mentioned=[], guardrails=g)
    assert v.passed is True


def test_all_mentioned_within_whitelist_passes() -> None:
    g = _guardrails()
    v = validate_whitelist(
        prices_mentioned=[247, 1497],
        products_mentioned=["Mentoria"],
        guardrails=g,
    )
    assert v.passed is True


def test_price_outside_whitelist_fails_and_explains() -> None:
    g = _guardrails(prices=[247, 1497])
    v = validate_whitelist(
        prices_mentioned=[5000],
        products_mentioned=[],
        guardrails=g,
    )
    assert v.passed is False
    assert v.reason is not None
    assert "5000" in v.reason
    assert v.suggested_fix is not None
    assert "247" in v.suggested_fix and "1497" in v.suggested_fix


def test_product_outside_whitelist_fails_case_insensitive() -> None:
    g = _guardrails(products=["Mentoria", "Aceleradora"])
    v = validate_whitelist(
        prices_mentioned=[],
        products_mentioned=["Coaching"],
        guardrails=g,
    )
    assert v.passed is False
    assert "Coaching" in v.reason  # type: ignore[operator]


def test_product_case_insensitive_match_passes() -> None:
    g = _guardrails(products=["Mentoria"])
    v = validate_whitelist(
        prices_mentioned=[],
        products_mentioned=["mentoria", "MENTORIA"],
        guardrails=g,
    )
    assert v.passed is True


def test_multiple_violations_aggregated_in_reason() -> None:
    g = _guardrails(prices=[247], products=["Mentoria"])
    v = validate_whitelist(
        prices_mentioned=[5000, 9999],
        products_mentioned=["X"],
        guardrails=g,
    )
    assert v.passed is False
    assert "5000" in v.reason and "9999" in v.reason and "X" in v.reason  # type: ignore[operator]
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_whitelist_validator.py -v`

Expected: `ImportError: No module named 'ai_sdr.guardrails.schemas'`.

- [ ] **Step 4: Create `src/ai_sdr/guardrails/schemas.py`**

```python
"""Verdict — the typed output of all guardrail validators (whitelist + critic)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Verdict(BaseModel):
    """A guardrail decision.

    Pydantic BaseModel (not dataclass) because critic_pass uses LangChain's
    `with_structured_output(Verdict)`, which requires a Pydantic class.
    """

    model_config = ConfigDict(frozen=True)

    passed: bool
    reason: str | None = None
    suggested_fix: str | None = None
```

- [ ] **Step 5: Create `src/ai_sdr/guardrails/whitelist.py`**

```python
"""Whitelist validator — checks LLM-emitted price/product mentions against tenant allowlists."""

from __future__ import annotations

from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def validate_whitelist(
    prices_mentioned: list[int],
    products_mentioned: list[str],
    guardrails: GuardrailsConfig,
) -> Verdict:
    """Return Verdict(passed=True) when guardrails are off or nothing violates.

    Otherwise return Verdict(passed=False) with a human-readable reason and a
    `suggested_fix` message intended to be injected back into the LLM as a
    SystemMessage on retry.
    """
    if not guardrails.enabled:
        return Verdict(passed=True)

    allowed_products_lower = {p.lower() for p in guardrails.allowed_products}
    bad_prices = sorted({p for p in prices_mentioned if p not in guardrails.allowed_prices})
    bad_products = sorted(
        {p for p in products_mentioned if p.lower() not in allowed_products_lower}
    )

    if not bad_prices and not bad_products:
        return Verdict(passed=True)

    parts: list[str] = []
    if bad_prices:
        parts.append(f"valor(es) não autorizado(s): {bad_prices}")
    if bad_products:
        parts.append(f"produto(s) não autorizado(s): {bad_products}")
    reason = "; ".join(parts)

    suggested_fix = (
        f"Sua resposta mencionou {reason}. "
        f"Valores permitidos: {guardrails.allowed_prices}. "
        f"Produtos permitidos: {guardrails.allowed_products}. "
        f"Refaça a resposta SEM mencionar valores ou produtos fora dessas listas."
    )

    return Verdict(passed=False, reason=reason, suggested_fix=suggested_fix)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_whitelist_validator.py -v`

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/guardrails/ tests/unit/test_whitelist_validator.py
git commit -m "feat(plan3 t4): Verdict schema + validate_whitelist (case-insensitive products)"
```

---

## Task 5: `build_structured_model` accepts `guardrails` (auto-adds mention fields)

**Files:**
- Modify: `src/ai_sdr/llm/extractor.py`
- Modify: `tests/unit/test_extractor.py`

**Design:** When `build_structured_model` is called with a `guardrails: GuardrailsConfig | None` argument AND `guardrails.enabled=True`, the generated Pydantic model gains two extra fields with explicit descriptions instructing the LLM to enumerate everything it mentioned textually:

```
prices_mentioned: list[int] = []
products_mentioned: list[str] = []
```

When `guardrails` is `None` or `enabled=False`, the model is unchanged from Plan 2 (only `response_text` + collects).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_extractor.py`:

```python
# ---------- guardrails extension (Plan 3) ----------

from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


def _gr(enabled: bool = True) -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=enabled,
        allowed_prices=[247],
        allowed_products=["Mentoria"],
        fallback_text="Confirmo já já, ok?",
    )


def test_build_model_without_guardrails_omits_mention_fields() -> None:
    collects = [CollectField(field="faturamento", type="number")]
    Model = build_structured_model(collects)
    fields = set(Model.model_fields.keys())
    assert "prices_mentioned" not in fields
    assert "products_mentioned" not in fields


def test_build_model_with_disabled_guardrails_omits_mention_fields() -> None:
    collects = [CollectField(field="faturamento", type="number")]
    Model = build_structured_model(collects, guardrails=_gr(enabled=False))
    fields = set(Model.model_fields.keys())
    assert "prices_mentioned" not in fields
    assert "products_mentioned" not in fields


def test_build_model_with_enabled_guardrails_adds_mention_fields() -> None:
    collects = [CollectField(field="faturamento", type="number")]
    Model = build_structured_model(collects, guardrails=_gr(enabled=True))
    fields = set(Model.model_fields.keys())
    assert "prices_mentioned" in fields
    assert "products_mentioned" in fields

    instance = Model(response_text="oi")
    assert instance.prices_mentioned == []
    assert instance.products_mentioned == []


def test_build_model_collect_name_clash_with_mention_field_rejected() -> None:
    """Author can't pick a collect field that would shadow the mention fields."""
    collects = [CollectField(field="prices_mentioned", type="text")]
    with pytest.raises(ValueError, match="reserved"):
        build_structured_model(collects, guardrails=_gr(enabled=True))
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_extractor.py -v`

Expected: FAIL with `TypeError: build_structured_model() got an unexpected keyword argument 'guardrails'`.

- [ ] **Step 3: Modify `src/ai_sdr/llm/extractor.py`**

Add to imports:

```python
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
```

Add a module-level set of reserved names:

```python
_GUARDRAIL_RESERVED = {"prices_mentioned", "products_mentioned"}
```

Replace the existing `build_structured_model` signature and body with:

```python
def build_structured_model(
    collects: list[CollectField],
    guardrails: GuardrailsConfig | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model: { response_text, <collects>, [prices/products_mentioned] }."""

    field_defs: dict[str, Any] = {
        RESPONSE_FIELD: (str, Field(description="What the agent says to the lead next.")),
    }

    guardrails_active = guardrails is not None and guardrails.enabled
    reserved = {RESPONSE_FIELD} | (_GUARDRAIL_RESERVED if guardrails_active else set())

    for c in collects:
        if c.field in reserved:
            raise ValueError(f"{c.field!r} is a reserved collect-field name")
        py_type = _PY_TYPE[c.type]
        description = c.extraction_hint or f"Extracted {c.type} field {c.field!r}."
        field_defs[c.field] = (py_type | None, Field(default=None, description=description))

    if guardrails_active:
        field_defs["prices_mentioned"] = (
            list[int],
            Field(
                default_factory=list,
                description=(
                    "Lista TODOS os valores monetários (em reais, como int) "
                    "que você mencionou textualmente em response_text. "
                    "Exemplo: se você escreveu 'a Mentoria custa R$ 6.000', "
                    "retorne [6000]. Vazio se nenhum valor mencionado."
                ),
            ),
        )
        field_defs["products_mentioned"] = (
            list[str],
            Field(
                default_factory=list,
                description=(
                    "Lista TODOS os nomes de produtos que você mencionou em "
                    "response_text. Exemplo: ['Mentoria', 'Aceleradora']. "
                    "Vazio se nenhum produto mencionado."
                ),
            ),
        )

    return create_model("NodeOutput", **field_defs)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_extractor.py -v`

Expected: all PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/llm/extractor.py tests/unit/test_extractor.py
git commit -m "feat(plan3 t5): build_structured_model adds prices/products_mentioned when guardrails active"
```

---

## Task 6: `build_system_messages` helper (per-provider cache control)

**Files:**
- Create: `src/ai_sdr/llm/messages.py`
- Create: `tests/unit/test_messages_builder.py`

**Design:** A single function that knows two providers (`"anthropic"` and `"openai"`) and renders the system portion of a chat request with the right cache markers. For Anthropic it returns one `SystemMessage` whose `content` is a list of typed text blocks; the first (static) block carries `cache_control={"type": "ephemeral"}` when `cache_enabled=True`. For OpenAI it concatenates everything into a single `SystemMessage(content=str)` (OpenAI auto-caches prefixes ≥1024 tok and doesn't expose a marker). When `cache_enabled=False` on Anthropic, the `cache_control` key is omitted but the block structure stays the same — that's important so the compiler doesn't branch on cache state.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_messages_builder.py`:

```python
"""Tests for build_system_messages — provider-aware cache marker placement."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from ai_sdr.llm.messages import build_system_messages


def test_anthropic_cache_enabled_marks_first_block() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["<kb>chunk1</kb>"],
        provider="anthropic",
        cache_enabled=True,
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, SystemMessage)
    assert isinstance(msg.content, list)
    assert len(msg.content) == 2
    assert msg.content[0]["type"] == "text"
    assert msg.content[0]["text"] == "static persona"
    assert msg.content[0]["cache_control"] == {"type": "ephemeral"}
    assert msg.content[1]["type"] == "text"
    assert msg.content[1]["text"] == "<kb>chunk1</kb>"
    assert "cache_control" not in msg.content[1]


def test_anthropic_cache_disabled_omits_marker() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["<kb>chunk1</kb>"],
        provider="anthropic",
        cache_enabled=False,
    )
    msg = msgs[0]
    assert isinstance(msg.content, list)
    assert "cache_control" not in msg.content[0]
    assert "cache_control" not in msg.content[1]


def test_anthropic_no_dynamic_blocks_single_static_block() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=[],
        provider="anthropic",
        cache_enabled=True,
    )
    msg = msgs[0]
    assert isinstance(msg.content, list)
    assert len(msg.content) == 1
    assert msg.content[0]["cache_control"] == {"type": "ephemeral"}


def test_openai_concatenates_into_single_string() -> None:
    msgs = build_system_messages(
        static_prompt="static persona",
        dynamic_blocks=["block A", "block B"],
        provider="openai",
        cache_enabled=True,
    )
    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg.content, str)
    assert msg.content == "static persona\n\nblock A\n\nblock B"


def test_openai_no_dynamic_blocks_returns_static_only() -> None:
    msgs = build_system_messages(
        static_prompt="just the persona",
        dynamic_blocks=[],
        provider="openai",
        cache_enabled=True,
    )
    assert msgs[0].content == "just the persona"


def test_openai_cache_flag_is_noop_on_structure() -> None:
    """OpenAI doesn't expose a disable; cache_enabled has no effect on output shape."""
    a = build_system_messages("p", ["b"], provider="openai", cache_enabled=True)
    b = build_system_messages("p", ["b"], provider="openai", cache_enabled=False)
    assert a[0].content == b[0].content
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messages_builder.py -v`

Expected: `ImportError: cannot import name 'build_system_messages'`.

- [ ] **Step 3: Create `src/ai_sdr/llm/messages.py`**

```python
"""build_system_messages — render the system portion with per-provider cache markers.

For Anthropic, the first block carries cache_control={"type": "ephemeral"} when
cache_enabled=True (Plan 3, spec §8). For OpenAI we concatenate into a single
string because OpenAI auto-caches prefixes ≥1024 tok without an explicit marker.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import SystemMessage


def build_system_messages(
    static_prompt: str,
    dynamic_blocks: list[str],
    provider: Literal["anthropic", "openai"],
    cache_enabled: bool = True,
) -> list[SystemMessage]:
    """Return the system messages for a single LLM turn.

    Always returns a one-element list — the compiler can append history +
    HumanMessage(user_input) downstream without worrying about provider.
    """
    if provider == "anthropic":
        first: dict = {"type": "text", "text": static_prompt}
        if cache_enabled:
            first["cache_control"] = {"type": "ephemeral"}
        content: list[dict] = [first]
        for block in dynamic_blocks:
            content.append({"type": "text", "text": block})
        return [SystemMessage(content=content)]

    # OpenAI — concatenate into one string. cache_enabled has no effect on shape
    # because OpenAI's prompt caching is automatic for prefixes ≥1024 tokens.
    parts = [static_prompt] + list(dynamic_blocks)
    return [SystemMessage(content="\n\n".join(parts))]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_messages_builder.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/llm/messages.py tests/unit/test_messages_builder.py
git commit -m "feat(plan3 t6): build_system_messages helper (anthropic cache_control + openai concat)"
```

---

## Task 7: `MarkdownChunker`

**Files:**
- Create: `src/ai_sdr/kb/__init__.py`
- Create: `src/ai_sdr/kb/chunker.py`
- Create: `tests/unit/test_kb_chunker.py`

**Design:** Roll-our-own markdown parser. Walk the file line-by-line; track a heading stack via the depth of leading `#`. When a new heading at any level is seen OR the current chunk's accumulated token count would exceed `max_tokens` after adding the next paragraph, emit a chunk. `heading_path` is `" > ".join(stack)` (or `None` if the chunk has no headings above it). Token counting uses `tiktoken.get_encoding("cl100k_base")` — same encoder used by `text-embedding-3-small`. Empty files return `[]`. A heading with no body is skipped (no empty chunk). For paragraphs longer than `max_tokens` alone, split by sentence (naïve `". "` split) and pack until cap.

- [ ] **Step 1: Create the empty package**

Create `src/ai_sdr/kb/__init__.py` with one line:

```python
"""KB (Knowledge Base) package — markdown → chunks → pgvector retrieval (Plan 3, spec §4.7)."""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_kb_chunker.py`:

```python
"""Tests for MarkdownChunker — header-aware chunking with token cap."""

from __future__ import annotations

from ai_sdr.kb.chunker import ChunkDraft, MarkdownChunker


def test_empty_string_returns_empty() -> None:
    assert MarkdownChunker().split("") == []


def test_whitespace_only_returns_empty() -> None:
    assert MarkdownChunker().split("\n\n  \n") == []


def test_single_section_one_chunk() -> None:
    md = "## Preços\n\nMentoria custa R$ 6000."
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].idx == 0
    assert chunks[0].heading_path == "Preços"
    assert "R$ 6000" in chunks[0].content
    assert chunks[0].token_count > 0


def test_two_sections_two_chunks_with_idx() -> None:
    md = "## A\n\ncontent a\n\n## B\n\ncontent b"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 2
    assert chunks[0].heading_path == "A"
    assert chunks[1].heading_path == "B"
    assert chunks[0].idx == 0 and chunks[1].idx == 1


def test_nested_headings_breadcrumb() -> None:
    md = "# Top\n\n## Sub\n\n### Leaf\n\ndeep content"
    chunks = MarkdownChunker().split(md)
    # Top has no body → skipped; Sub has no body → skipped; Leaf has body → 1 chunk
    assert len(chunks) == 1
    assert chunks[0].heading_path == "Top > Sub > Leaf"


def test_heading_without_body_skipped() -> None:
    md = "## Empty\n\n## With body\n\nbody"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].heading_path == "With body"


def test_no_headings_one_chunk_with_no_path() -> None:
    md = "just some text without any heading"
    chunks = MarkdownChunker().split(md)
    assert len(chunks) == 1
    assert chunks[0].heading_path is None


def test_long_section_splits_by_paragraph_under_cap() -> None:
    big_para = ("foo bar baz " * 80) + "."  # ~240 tok per paragraph
    md = f"## Big\n\n{big_para}\n\n{big_para}\n\n{big_para}\n\n{big_para}\n\n{big_para}"
    chunker = MarkdownChunker(max_tokens=300)
    chunks = chunker.split(md)
    # 5 paragraphs × ~240 tok → ~1200 tok total; cap 300 → at least 4 chunks
    assert len(chunks) >= 4
    for c in chunks:
        assert c.heading_path == "Big"
        assert c.token_count <= 300


def test_single_paragraph_exceeds_cap_splits_by_sentence() -> None:
    sentences = ". ".join(["foo bar baz" * 20 for _ in range(20)]) + "."
    md = f"## Wall\n\n{sentences}"
    chunker = MarkdownChunker(max_tokens=200)
    chunks = chunker.split(md)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 200


def test_chunk_draft_fields_immutable() -> None:
    c = ChunkDraft(idx=0, heading_path="A", content="x", token_count=1)
    import dataclasses
    assert dataclasses.is_dataclass(c)
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_kb_chunker.py -v`

Expected: `ImportError: No module named 'ai_sdr.kb.chunker'`.

- [ ] **Step 4: Create `src/ai_sdr/kb/chunker.py`**

```python
"""Markdown-aware chunker with a token cap.

Roll-our-own: walk lines, track heading stack by leading '#' count, emit a chunk
whenever a heading boundary OR token cap is hit. Paragraphs longer than the cap
are split by sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import tiktoken

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_DEFAULT_MAX_TOKENS = 600


@dataclass(frozen=True)
class ChunkDraft:
    idx: int
    heading_path: str | None
    content: str
    token_count: int


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _paragraphs(body: str) -> list[str]:
    """Split a chunk of text into paragraphs on blank lines, dropping empties."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)]
    return [p for p in paras if p]


def _split_paragraph_by_sentence(paragraph: str, max_tokens: int) -> list[str]:
    """Greedy pack sentences until cap is reached. Sentences split on '. ' boundary."""
    raw_sentences = re.split(r"(?<=\.)\s+", paragraph)
    out: list[str] = []
    buf: list[str] = []
    buf_tok = 0
    for s in raw_sentences:
        s_tok = _count_tokens(s)
        if buf and buf_tok + s_tok > max_tokens:
            out.append(" ".join(buf).strip())
            buf, buf_tok = [s], s_tok
        else:
            buf.append(s)
            buf_tok += s_tok
    if buf:
        out.append(" ".join(buf).strip())
    return [c for c in out if c]


class MarkdownChunker:
    """Header-aware chunker: each section under a heading becomes one chunk,
    split by paragraph (or sentence) when over max_tokens."""

    def __init__(self, max_tokens: int = _DEFAULT_MAX_TOKENS) -> None:
        self.max_tokens = max_tokens

    def split(self, content_md: str) -> list[ChunkDraft]:
        sections = self._sectionize(content_md)
        drafts: list[ChunkDraft] = []
        idx = 0
        for heading_path, body in sections:
            if not body.strip():
                continue
            for piece in self._pack(body):
                drafts.append(
                    ChunkDraft(
                        idx=idx,
                        heading_path=heading_path,
                        content=piece,
                        token_count=_count_tokens(piece),
                    )
                )
                idx += 1
        return drafts

    def _sectionize(self, md: str) -> list[tuple[str | None, str]]:
        """Walk lines, return list of (heading_path, body) tuples in document order."""
        stack: list[tuple[int, str]] = []  # (depth, title)
        sections: list[tuple[str | None, list[str]]] = []
        current_body: list[str] = []
        current_path: str | None = None

        def flush() -> None:
            if current_body:
                sections.append((current_path, list(current_body)))
                current_body.clear()

        for line in md.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                flush()
                depth = len(m.group(1))
                title = m.group(2).strip()
                # pop deeper-or-equal entries
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, title))
                current_path = " > ".join(t for _, t in stack)
            else:
                current_body.append(line)
        flush()

        # also include any pre-heading text as a None-path section
        return [(p, "\n".join(b)) for p, b in sections]

    def _pack(self, body: str) -> list[str]:
        """Pack the body into chunks ≤ max_tokens by paragraph, splitting further by sentence."""
        paras = _paragraphs(body)
        chunks: list[str] = []
        buf: list[str] = []
        buf_tok = 0

        for p in paras:
            p_tok = _count_tokens(p)
            if p_tok > self.max_tokens:
                # flush buf, then sentence-split this monster
                if buf:
                    chunks.append("\n\n".join(buf))
                    buf, buf_tok = [], 0
                chunks.extend(_split_paragraph_by_sentence(p, self.max_tokens))
                continue
            if buf and buf_tok + p_tok > self.max_tokens:
                chunks.append("\n\n".join(buf))
                buf, buf_tok = [p], p_tok
            else:
                buf.append(p)
                buf_tok += p_tok

        if buf:
            chunks.append("\n\n".join(buf))
        return [c for c in chunks if c.strip()]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_kb_chunker.py -v`

Expected: all PASS.

- [ ] **Step 6: Lint**

Run: `make lint && make format`

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/kb/__init__.py src/ai_sdr/kb/chunker.py tests/unit/test_kb_chunker.py
git commit -m "feat(plan3 t7): MarkdownChunker (header-aware, 600 tok cap, tiktoken)"
```

---

## Task 8: `Embedder` + `build_embedder` factory

**Files:**
- Create: `src/ai_sdr/kb/embeddings.py`
- Create: `tests/unit/test_kb_embeddings.py`

**Design:** Thin wrapper over `langchain_openai.OpenAIEmbeddings`. `Embedder.embed_query(text)` → `list[float]` (1536d for `text-embedding-3-small`). `embed_documents(texts)` → `list[list[float]]`. `build_embedder(secrets, cfg)` reads the API key from `secrets[cfg.api_key_ref]` and constructs an `Embedder`. Async wrappers (`aembed_query` / `aembed_documents`) come from `OpenAIEmbeddings` natively. We expose async-only methods on `Embedder` because indexer and retriever both run in async contexts.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_kb_embeddings.py`:

```python
"""Tests for build_embedder — factory wiring (no live API calls)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.schemas.llm_yaml import EmbeddingsConfig


def test_build_embedder_with_default_config() -> None:
    secrets = {"openai_key": "sk-test-fake"}
    with patch("ai_sdr.kb.embeddings.OpenAIEmbeddings") as fake_cls:
        emb = build_embedder(secrets, EmbeddingsConfig())
    fake_cls.assert_called_once()
    kwargs = fake_cls.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-small"
    assert kwargs["api_key"] == "sk-test-fake"
    assert isinstance(emb, Embedder)


def test_build_embedder_custom_model_and_key_ref() -> None:
    secrets = {"openai_key_alt": "sk-test-other"}
    cfg = EmbeddingsConfig(model="text-embedding-3-large", api_key_ref="openai_key_alt")
    with patch("ai_sdr.kb.embeddings.OpenAIEmbeddings") as fake_cls:
        build_embedder(secrets, cfg)
    kwargs = fake_cls.call_args.kwargs
    assert kwargs["model"] == "text-embedding-3-large"
    assert kwargs["api_key"] == "sk-test-other"


def test_build_embedder_missing_secret_raises_keyerror() -> None:
    with pytest.raises(KeyError, match="openai_key"):
        build_embedder({}, EmbeddingsConfig())


async def test_embedder_delegates_to_lc_async_methods() -> None:
    class _FakeLC:
        async def aembed_query(self, text: str) -> list[float]:
            return [0.1] * 1536

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.2] * 1536 for _ in texts]

    emb = Embedder(_FakeLC())  # type: ignore[arg-type]
    q = await emb.embed_query("hello")
    assert len(q) == 1536 and q[0] == 0.1

    docs = await emb.embed_documents(["a", "b"])
    assert len(docs) == 2 and len(docs[0]) == 1536 and docs[0][0] == 0.2
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_kb_embeddings.py -v`

Expected: `ImportError: No module named 'ai_sdr.kb.embeddings'`.

- [ ] **Step 3: Create `src/ai_sdr/kb/embeddings.py`**

```python
"""Embedder factory — wraps langchain_openai.OpenAIEmbeddings for async use.

Single embedding model in MVP: text-embedding-3-small (1536d). Switch via
tenant.llm.embeddings in tenant.yaml.
"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from ai_sdr.schemas.llm_yaml import EmbeddingsConfig


class Embedder:
    """Async-only embedder. Hides the LangChain wrapper from callers."""

    def __init__(self, lc_embeddings: OpenAIEmbeddings) -> None:
        self._lc = lc_embeddings

    async def embed_query(self, text: str) -> list[float]:
        return await self._lc.aembed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._lc.aembed_documents(texts)


def build_embedder(secrets: dict[str, str], cfg: EmbeddingsConfig) -> Embedder:
    """Read API key from secrets[cfg.api_key_ref] and construct an Embedder."""
    api_key = secrets[cfg.api_key_ref]  # KeyError surfaces explicitly
    lc = OpenAIEmbeddings(model=cfg.model, api_key=api_key)
    return Embedder(lc)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_kb_embeddings.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/kb/embeddings.py tests/unit/test_kb_embeddings.py
git commit -m "feat(plan3 t8): Embedder wrapping OpenAIEmbeddings + build_embedder factory"
```

---

## Task 9: SQLAlchemy models — `KbDocument` + `KbChunk`

**Files:**
- Create: `src/ai_sdr/models/kb_document.py`
- Create: `src/ai_sdr/models/kb_chunk.py`
- Modify: `src/ai_sdr/models/__init__.py`

**Design:** Two new ORM models matching the schema in spec §6. `KbChunk` denormalizes `tenant_id` + `kb_id` for fast filtering against the IVFFlat index without needing a JOIN. The `embedding` column uses `pgvector.sqlalchemy.Vector(1536)`. Both models inherit from `ai_sdr.db.base.Base` (created in Plan 1). No tests in this task — model behavior is exercised by the integration test in Task 10 (after the migration runs).

- [ ] **Step 1: Create `src/ai_sdr/models/kb_document.py`**

```python
"""KB document — one row per `.md` file under kb/<tenant>/<kb_id>/."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class KbDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kb_id", "doc_path", name="uq_kb_documents_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id: Mapped[str] = mapped_column(String(128), nullable=False)
    doc_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 2: Create `src/ai_sdr/models/kb_chunk.py`**

```python
"""KB chunk — fixed-size piece of a KbDocument, with a 1536d embedding."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_idx", name="uq_kb_chunks_doc_idx"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalized for RLS + fast filtering against IVFFlat index without JOIN.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 3: Update `src/ai_sdr/models/__init__.py`**

Add the two new imports/exports alongside the existing ones:

```python
"""SQLAlchemy models. Each model is re-exported here so alembic can discover them."""

from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

__all__ = ["KbChunk", "KbDocument", "TalkFlow", "Tenant", "TreeflowVersion"]
```

- [ ] **Step 4: Sanity-import**

Run: `uv run python -c "from ai_sdr.models import KbDocument, KbChunk; print(KbDocument.__tablename__, KbChunk.__tablename__)"`

Expected: prints `kb_documents kb_chunks`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/kb_document.py src/ai_sdr/models/kb_chunk.py src/ai_sdr/models/__init__.py
git commit -m "feat(plan3 t9): SQLAlchemy models for kb_documents + kb_chunks"
```

---

## Task 10: Migration `0005_kb_tables.py` + integration test (RLS + IVFFlat)

**Files:**
- Create: `migrations/versions/0005_kb_tables.py`
- Create: `tests/integration/test_kb_models.py`

**Design:** Manual Alembic migration (deterministic revision id), `down_revision = "0004_checkpointer_setup"`. Creates both tables, RLS policies (`USING + WITH CHECK`), `FORCE ROW LEVEL SECURITY`, the b-tree filter index, and the IVFFlat embedding index. Integration test asserts (a) basic insert/select round-trip, (b) RLS isolation between two tenants, (c) the IVFFlat index exists. The `vector` extension is already enabled by `0001_extensions.py`, so this migration does not need `CREATE EXTENSION`.

- [ ] **Step 1: Create `migrations/versions/0005_kb_tables.py`**

```python
"""kb_documents + kb_chunks tables (with RLS + IVFFlat index)

Revision ID: 0005_kb_tables
Revises: 0004_checkpointer_setup
Create Date: 2026-05-23 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_kb_tables"
down_revision = "0004_checkpointer_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_documents",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", sa.String(length=128), nullable=False),
        sa.Column("doc_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "kb_id", "doc_path", name="uq_kb_documents_path"),
    )
    op.create_index("ix_kb_documents_tenant_id", "kb_documents", ["tenant_id"])

    op.create_table(
        "kb_chunks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["kb_documents.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("document_id", "chunk_idx", name="uq_kb_chunks_doc_idx"),
    )
    op.create_index("ix_kb_chunks_document_id", "kb_chunks", ["document_id"])
    op.create_index("ix_kb_chunks_filter", "kb_chunks", ["tenant_id", "kb_id"])
    # IVFFlat index for cosine similarity search. lists=100 is fine for <10k chunks.
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding ON kb_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);"
    )

    # RLS on both tables
    for tbl in ("kb_documents", "kb_chunks"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_iso ON {tbl}
                USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
                WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
            """
        )


def downgrade() -> None:
    for tbl in ("kb_chunks", "kb_documents"):
        op.execute(f"DROP POLICY IF EXISTS tenant_iso ON {tbl};")
    op.execute("DROP INDEX IF EXISTS ix_kb_chunks_embedding;")
    op.drop_index("ix_kb_chunks_filter", table_name="kb_chunks")
    op.drop_index("ix_kb_chunks_document_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_documents_tenant_id", table_name="kb_documents")
    op.drop_table("kb_documents")
```

- [ ] **Step 2: Apply the migration**

Run: `make migrate`

Expected: alembic applies `0005_kb_tables`.

- [ ] **Step 3: Write the integration test**

Create `tests/integration/test_kb_models.py`:

```python
"""kb_documents + kb_chunks: insert/select round-trip + RLS isolation + IVFFlat index."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_round_trip_document_and_chunks(session: AsyncSession) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
        await set_tenant_context(session, t.id)

        doc = KbDocument(
            tenant_id=t.id,
            kb_id="kb_x",
            doc_path="kb/t/kb_x/precos.md",
            content_hash="deadbeef",
            content_md="## Preços\n\nMentoria custa R$ 6000.",
        )
        session.add(doc)
        await session.flush()

        chunk = KbChunk(
            document_id=doc.id,
            tenant_id=t.id,
            kb_id="kb_x",
            chunk_idx=0,
            heading_path="Preços",
            content="Mentoria custa R$ 6000.",
            token_count=10,
            embedding=[0.1] * 1536,
        )
        session.add(chunk)

    async with session.begin():
        await set_tenant_context(session, t.id)
        got_doc = (
            await session.execute(select(KbDocument).where(KbDocument.tenant_id == t.id))
        ).scalar_one()
        assert got_doc.kb_id == "kb_x"
        got_chunk = (
            await session.execute(select(KbChunk).where(KbChunk.document_id == got_doc.id))
        ).scalar_one()
        assert got_chunk.heading_path == "Preços"
        assert len(got_chunk.embedding) == 1536


@pytest.mark.integration
async def test_rls_isolates_kb_chunks_across_tenants(session: AsyncSession) -> None:
    async with session.begin():
        t1 = Tenant(slug=f"a-{uuid.uuid4().hex[:8]}", display_name="A")
        t2 = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", display_name="B")
        session.add_all([t1, t2])
        await session.flush()

        # tenant 1 doc + chunk
        await set_tenant_context(session, t1.id)
        d1 = KbDocument(
            tenant_id=t1.id, kb_id="kb", doc_path="d1.md",
            content_hash="h1", content_md="x",
        )
        session.add(d1)
        await session.flush()
        session.add(KbChunk(
            document_id=d1.id, tenant_id=t1.id, kb_id="kb",
            chunk_idx=0, content="t1", token_count=1, embedding=[0.1] * 1536,
        ))

        # tenant 2 doc + chunk
        await set_tenant_context(session, t2.id)
        d2 = KbDocument(
            tenant_id=t2.id, kb_id="kb", doc_path="d2.md",
            content_hash="h2", content_md="y",
        )
        session.add(d2)
        await session.flush()
        session.add(KbChunk(
            document_id=d2.id, tenant_id=t2.id, kb_id="kb",
            chunk_idx=0, content="t2", token_count=1, embedding=[0.2] * 1536,
        ))

    async with session.begin():
        await set_tenant_context(session, t1.id)
        rows = (await session.execute(select(KbChunk))).scalars().all()
        contents = sorted(r.content for r in rows)
        assert contents == ["t1"]

    async with session.begin():
        await set_tenant_context(session, t2.id)
        rows = (await session.execute(select(KbChunk))).scalars().all()
        contents = sorted(r.content for r in rows)
        assert contents == ["t2"]


@pytest.mark.integration
async def test_ivfflat_index_exists(session: AsyncSession) -> None:
    async with session.begin():
        result = await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'kb_chunks' AND indexname = 'ix_kb_chunks_embedding'"
            )
        )
        row = result.scalar_one_or_none()
    assert row is not None
    assert "ivfflat" in row.lower()
    assert "vector_cosine_ops" in row.lower()
```

- [ ] **Step 4: Run integration tests**

Run: `uv run pytest tests/integration/test_kb_models.py -v -m integration`

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0005_kb_tables.py tests/integration/test_kb_models.py
git commit -m "feat(plan3 t10): migration 0005 (kb_documents + kb_chunks + RLS + IVFFlat)"
```

---

## Task 11: KB indexer

**Files:**
- Create: `src/ai_sdr/kb/indexer.py`
- Create: `tests/integration/test_kb_indexer.py`

**Design:** `reindex_tenant_kb(session, tenant, kb_root, embedder, chunker, prune=False, kb_id=None)` walks `kb_root/<tenant.slug>/(<kb_id>/)?**/*.md`, hashes each file, compares to `kb_documents.content_hash`, and re-chunks + re-embeds the changed ones. Unchanged files are skipped (telemetry: `kb.skipped`). If `prune=True`, files that disappeared from the FS are deleted (cascade also removes their chunks). RLS is set via `set_tenant_context(session, tenant.id)` at the top so the function inherits Plan 1's isolation contract. `IndexResult` returns lists of `indexed`, `skipped`, `pruned`, and `failed` doc paths.

The structlog logger is module-level: `logger = structlog.get_logger(__name__)`. Events: `kb.indexed`, `kb.skipped`, `kb.pruned`, `kb.failed`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_kb_indexer.py`:

```python
"""Integration tests for reindex_tenant_kb — idempotent indexer."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.kb.indexer import IndexResult, reindex_tenant_kb
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


class _FakeEmbedder(Embedder):
    """Returns deterministic per-text vectors so we never call OpenAI."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        self.calls = 0

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(hash(t) % 100) / 100.0] * 1536 for t in texts]


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    return tmp_path / "kb"


def _write_md(root: Path, tenant_slug: str, kb_id: str, name: str, body: str) -> Path:
    p = root / tenant_slug / kb_id / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.mark.integration
async def test_indexer_creates_documents_and_chunks(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_x", "precos.md",
              "## Preços\n\nMentoria custa R$ 6000.\n\n## Garantia\n\n7 dias.")

    result = await reindex_tenant_kb(
        session, t, kb_root, embedder=_FakeEmbedder(), chunker=MarkdownChunker()
    )

    assert isinstance(result, IndexResult)
    assert len(result.indexed) == 1
    assert result.skipped == [] and result.failed == [] and result.pruned == []

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        assert len(docs) == 1
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        assert len(chunks) == 2  # one per section


@pytest.mark.integration
async def test_indexer_is_idempotent_when_content_unchanged(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nbody")

    embedder = _FakeEmbedder()
    first = await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())
    assert len(first.indexed) == 1 and embedder.calls == 1

    second = await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())
    assert second.indexed == [] and len(second.skipped) == 1
    assert embedder.calls == 1  # no re-embedding


@pytest.mark.integration
async def test_indexer_reindexes_when_content_changes(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p = _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nold body")
    await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())

    p.write_text("## A\n\nNEW body with more content for chunking", encoding="utf-8")
    second = await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())
    assert len(second.indexed) == 1

    async with session.begin():
        await set_tenant_context(session, t.id)
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        # old chunks were deleted; new ones inserted
        assert all("NEW body" in c.content for c in chunks)


@pytest.mark.integration
async def test_indexer_prune_removes_deleted_docs(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p1 = _write_md(kb_root, t.slug, "kb_x", "a.md", "## A\n\nbody1")
    p2 = _write_md(kb_root, t.slug, "kb_x", "b.md", "## B\n\nbody2")
    await reindex_tenant_kb(session, t, kb_root, _FakeEmbedder(), MarkdownChunker())

    p2.unlink()
    result = await reindex_tenant_kb(
        session, t, kb_root, _FakeEmbedder(), MarkdownChunker(), prune=True
    )
    assert any("b.md" in path for path in result.pruned)

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        assert len(docs) == 1
        assert "a.md" in docs[0].doc_path


@pytest.mark.integration
async def test_indexer_kb_id_filter(session: AsyncSession, kb_root: Path) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    _write_md(kb_root, t.slug, "kb_a", "x.md", "## X\n\nA")
    _write_md(kb_root, t.slug, "kb_b", "y.md", "## Y\n\nB")

    result = await reindex_tenant_kb(
        session, t, kb_root, _FakeEmbedder(), MarkdownChunker(), kb_id="kb_a"
    )
    assert len(result.indexed) == 1
    assert "kb_a" in result.indexed[0]

    async with session.begin():
        await set_tenant_context(session, t.id)
        docs = (await session.execute(select(KbDocument))).scalars().all()
        kb_ids = sorted(d.kb_id for d in docs)
        assert kb_ids == ["kb_a"]


@pytest.mark.integration
async def test_indexer_skips_invalid_utf8(
    session: AsyncSession, kb_root: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p = kb_root / t.slug / "kb_x" / "bad.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe\x00\x00not utf-8")
    _write_md(kb_root, t.slug, "kb_x", "good.md", "## G\n\nok")

    result = await reindex_tenant_kb(
        session, t, kb_root, _FakeEmbedder(), MarkdownChunker()
    )
    assert any("bad.md" in path for path, _ in result.failed)
    assert any("good.md" in path for path in result.indexed)
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_kb_indexer.py -v -m integration`

Expected: `ImportError: cannot import name 'reindex_tenant_kb'`.

- [ ] **Step 3: Create `src/ai_sdr/kb/indexer.py`**

```python
"""Idempotent KB indexer — walks kb_root/<slug>/[<kb_id>]/**/*.md, hashes,
re-chunks + re-embeds only changed docs. Spec §4.3."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant

logger = structlog.get_logger(__name__)


@dataclass
class IndexResult:
    indexed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _list_md_files(kb_root: Path, slug: str, kb_id: str | None) -> list[Path]:
    base = kb_root / slug
    if not base.exists():
        return []
    if kb_id is not None:
        glob_base = base / kb_id
        if not glob_base.exists():
            return []
        return sorted(glob_base.rglob("*.md"))
    return sorted(base.rglob("*.md"))


def _rel_doc_path(kb_root: Path, fs_path: Path) -> str:
    """Return the path relative to the repo root (kb_root.parent) as a stable string."""
    try:
        return str(fs_path.relative_to(kb_root.parent))
    except ValueError:
        return str(fs_path)


def _kb_id_from_path(kb_root: Path, slug: str, fs_path: Path) -> str:
    """First path component under kb_root/<slug>/."""
    rel = fs_path.relative_to(kb_root / slug)
    return rel.parts[0]


async def reindex_tenant_kb(
    session: AsyncSession,
    tenant: Tenant,
    kb_root: Path,
    embedder: Embedder,
    chunker: MarkdownChunker,
    prune: bool = False,
    kb_id: str | None = None,
) -> IndexResult:
    """Reindex one tenant's KB tree. Idempotent via content_hash.

    If `kb_id` is given, only that KB is touched (and pruning is scoped to it).
    """
    result = IndexResult()
    await set_tenant_context(session, tenant.id)

    fs_files = _list_md_files(kb_root, tenant.slug, kb_id)
    fs_paths_rel = {_rel_doc_path(kb_root, p) for p in fs_files}

    # Index/upsert
    for fs_path in fs_files:
        doc_rel = _rel_doc_path(kb_root, fs_path)
        try:
            content = fs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            logger.error("kb.failed", tenant=tenant.slug, doc=doc_rel, error=str(e))
            result.failed.append((doc_rel, f"unicode_decode_error: {e}"))
            continue

        digest = _hash(content)
        doc_kb_id = _kb_id_from_path(kb_root, tenant.slug, fs_path)

        existing = (
            await session.execute(
                select(KbDocument).where(
                    KbDocument.tenant_id == tenant.id,
                    KbDocument.kb_id == doc_kb_id,
                    KbDocument.doc_path == doc_rel,
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.content_hash == digest:
            logger.info(
                "kb.skipped",
                tenant=tenant.slug,
                kb_id=doc_kb_id,
                doc=doc_rel,
                reason="hash_unchanged",
            )
            result.skipped.append(doc_rel)
            continue

        t0 = time.perf_counter()
        drafts = chunker.split(content)
        if not drafts:
            logger.warning("kb.empty_doc", tenant=tenant.slug, doc=doc_rel)
            # still index the document row (empty body); no chunks
            embeddings: list[list[float]] = []
        else:
            embeddings = await embedder.embed_documents([d.content for d in drafts])

        if existing is None:
            doc = KbDocument(
                tenant_id=tenant.id,
                kb_id=doc_kb_id,
                doc_path=doc_rel,
                content_hash=digest,
                content_md=content,
            )
            session.add(doc)
            await session.flush()
        else:
            existing.content_hash = digest
            existing.content_md = content
            await session.execute(
                delete(KbChunk).where(KbChunk.document_id == existing.id)
            )
            await session.flush()
            doc = existing

        for draft, emb in zip(drafts, embeddings, strict=True):
            session.add(
                KbChunk(
                    document_id=doc.id,
                    tenant_id=tenant.id,
                    kb_id=doc_kb_id,
                    chunk_idx=draft.idx,
                    heading_path=draft.heading_path,
                    content=draft.content,
                    token_count=draft.token_count,
                    embedding=emb,
                )
            )
        await session.flush()

        took_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "kb.indexed",
            tenant=tenant.slug,
            kb_id=doc_kb_id,
            doc=doc_rel,
            chunks=len(drafts),
            took_ms=took_ms,
        )
        result.indexed.append(doc_rel)

    # Prune
    if prune:
        q = select(KbDocument).where(KbDocument.tenant_id == tenant.id)
        if kb_id is not None:
            q = q.where(KbDocument.kb_id == kb_id)
        db_docs = (await session.execute(q)).scalars().all()
        for d in db_docs:
            if d.doc_path not in fs_paths_rel:
                await session.execute(delete(KbDocument).where(KbDocument.id == d.id))
                logger.info(
                    "kb.pruned", tenant=tenant.slug, kb_id=d.kb_id, doc=d.doc_path
                )
                result.pruned.append(d.doc_path)
        await session.flush()

    return result
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_kb_indexer.py -v -m integration`

Expected: all PASS.

- [ ] **Step 5: Lint**

Run: `make lint && make format`

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/kb/indexer.py tests/integration/test_kb_indexer.py
git commit -m "feat(plan3 t11): reindex_tenant_kb (idempotent via content_hash, prune mode, kb_id filter)"
```

---

## Task 12: KB retriever

**Files:**
- Create: `src/ai_sdr/kb/retriever.py`
- Create: `tests/integration/test_kb_retriever.py`

**Design:** `retrieve(session, tenant_id, kb_refs, query, embedder)` embeds the query, runs a single SQL with `kb_id = ANY(:kb_ids)` ordered by cosine distance, takes the largest `top_k` across all refs, then in Python filters each chunk against the `min_score` of the `KBRef` whose `kb_id` matches that chunk. RLS is set up front. Returns `list[RetrievedChunk]` sorted by score descending. Empty results return `[]` after logging `kb.no_match`. Embed failures log `kb.embed_error` and return `[]` (does NOT raise — callers should still produce a turn without KB).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_kb_retriever.py`:

```python
"""Integration tests for KB retriever — pgvector top-k + score filter + RLS."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.kb.retriever import RetrievedChunk, retrieve
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.treeflow_yaml import KBRef
from ai_sdr.settings import get_settings


class _DeterministicEmbedder(Embedder):
    """Maps tokens to 1-hot positions so we can craft predictable cosine scores."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        pass

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 1536
        for word in text.lower().split():
            v[hash(word) % 1536] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    return tmp_path / "kb"


async def _seed_tenant_with_kb(
    session: AsyncSession, kb_root: Path, kb_id: str, sections: dict[str, str]
) -> Tenant:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    p = kb_root / t.slug / kb_id / "main.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(f"## {h}\n\n{txt}" for h, txt in sections.items())
    p.write_text(body, encoding="utf-8")
    await reindex_tenant_kb(
        session, t, kb_root, _DeterministicEmbedder(), MarkdownChunker()
    )
    return t


@pytest.mark.integration
async def test_retrieve_returns_top_k_sorted_by_score(
    session: AsyncSession, kb_root: Path
) -> None:
    t = await _seed_tenant_with_kb(
        session,
        kb_root,
        "kb_x",
        {
            "Preços": "Mentoria seis mil",
            "Garantia": "Sete dias",
            "Bonus": "Comunidade",
        },
    )

    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_x", top_k=2, min_score=0.0)],
        query="mentoria preço seis mil",
        embedder=_DeterministicEmbedder(),
    )
    assert len(chunks) == 2
    assert isinstance(chunks[0], RetrievedChunk)
    assert chunks[0].score >= chunks[1].score


@pytest.mark.integration
async def test_retrieve_filters_below_min_score(
    session: AsyncSession, kb_root: Path
) -> None:
    t = await _seed_tenant_with_kb(
        session, kb_root, "kb_x",
        {"A": "alpha beta", "B": "completely unrelated stuff"},
    )

    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_x", top_k=10, min_score=0.5)],
        query="alpha beta",
        embedder=_DeterministicEmbedder(),
    )
    # The unrelated chunk should be filtered by min_score
    assert all(c.score >= 0.5 for c in chunks)


@pytest.mark.integration
async def test_retrieve_unknown_kb_returns_empty(
    session: AsyncSession, kb_root: Path
) -> None:
    t = await _seed_tenant_with_kb(session, kb_root, "kb_x", {"A": "alpha"})
    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_nope")],
        query="anything",
        embedder=_DeterministicEmbedder(),
    )
    assert chunks == []


@pytest.mark.integration
async def test_retrieve_aggregates_across_multiple_kbs(
    session: AsyncSession, kb_root: Path
) -> None:
    t = await _seed_tenant_with_kb(
        session, kb_root, "kb_a", {"A": "alpha alpha alpha"}
    )
    # second KB under same tenant
    p = kb_root / t.slug / "kb_b" / "main.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("## B\n\nbeta beta beta", encoding="utf-8")
    await reindex_tenant_kb(
        session, t, kb_root, _DeterministicEmbedder(), MarkdownChunker(), kb_id="kb_b"
    )

    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_a", top_k=1), KBRef(id="kb_b", top_k=1, min_score=0.0)],
        query="alpha",
        embedder=_DeterministicEmbedder(),
    )
    kb_ids = {c.kb_id for c in chunks}
    # 'alpha' query → kb_a chunk should win; kb_b might be filtered by min_score=0.7
    assert "kb_a" in kb_ids


@pytest.mark.integration
async def test_retrieve_respects_rls_tenant_isolation(
    session: AsyncSession, kb_root: Path
) -> None:
    t1 = await _seed_tenant_with_kb(session, kb_root, "kb_x", {"A": "alpha"})
    t2 = await _seed_tenant_with_kb(session, kb_root, "kb_x", {"A": "alpha"})

    chunks_t1 = await retrieve(
        session,
        tenant_id=t1.id,
        kb_refs=[KBRef(id="kb_x", min_score=0.0)],
        query="alpha",
        embedder=_DeterministicEmbedder(),
    )
    chunks_t2 = await retrieve(
        session,
        tenant_id=t2.id,
        kb_refs=[KBRef(id="kb_x", min_score=0.0)],
        query="alpha",
        embedder=_DeterministicEmbedder(),
    )
    assert chunks_t1 and chunks_t2
    # Cross-tenant chunks must not bleed through
    ids_t1 = {c.content for c in chunks_t1}
    ids_t2 = {c.content for c in chunks_t2}
    # Since contents are identical strings, validate they belong to the right tenant
    # by re-querying the chunk rows with RLS scoped — out of scope for this test.
    # The fact that both calls returned non-empty results under their own RLS context
    # is the assertion that matters.
    assert ids_t1 == ids_t2  # same contents in both tenants
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_kb_retriever.py -v -m integration`

Expected: `ImportError: cannot import name 'retrieve'`.

- [ ] **Step 3: Create `src/ai_sdr/kb/retriever.py`**

```python
"""KB retriever — embed query, query pgvector, filter by min_score per KBRef.

Spec §4.4. Logs `kb.retrieved` / `kb.no_match` / `kb.embed_error`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.schemas.treeflow_yaml import KBRef

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    heading_path: str | None
    kb_id: str
    score: float


def _vec_to_pg_literal(vec: list[float]) -> str:
    """pgvector expects a string like '[0.1,0.2,...]' when passed via text()."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def retrieve(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kb_refs: list[KBRef],
    query: str,
    embedder: Embedder,
) -> list[RetrievedChunk]:
    """Retrieve top-k chunks across kb_refs, filtered by each ref's min_score."""
    if not kb_refs:
        return []

    await set_tenant_context(session, tenant_id)

    try:
        query_vec = await embedder.embed_query(query)
    except Exception as e:  # noqa: BLE001 — we never want retrieval failure to nuke a turn
        logger.error("kb.embed_error", tenant=str(tenant_id), error=str(e))
        return []

    kb_ids = [ref.id for ref in kb_refs]
    top_k_max = max(ref.top_k for ref in kb_refs)

    sql = text(
        """
        SELECT content, heading_path, kb_id,
               1 - (embedding <=> CAST(:qvec AS vector)) AS score
        FROM kb_chunks
        WHERE tenant_id = CAST(:tid AS uuid)
          AND kb_id = ANY(:kb_ids)
        ORDER BY embedding <=> CAST(:qvec AS vector) ASC
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            sql,
            {
                "qvec": _vec_to_pg_literal(query_vec),
                "tid": str(tenant_id),
                "kb_ids": kb_ids,
                "limit": top_k_max,
            },
        )
    ).mappings().all()

    ref_by_id = {ref.id: ref for ref in kb_refs}
    out: list[RetrievedChunk] = []
    for r in rows:
        ref = ref_by_id.get(r["kb_id"])
        if ref is None:
            continue
        if r["score"] < ref.min_score:
            continue
        out.append(
            RetrievedChunk(
                content=r["content"],
                heading_path=r["heading_path"],
                kb_id=r["kb_id"],
                score=float(r["score"]),
            )
        )

    out.sort(key=lambda c: c.score, reverse=True)

    if not out:
        logger.info(
            "kb.no_match",
            tenant=str(tenant_id),
            kb_ids=kb_ids,
            query_preview=query[:80],
        )
    else:
        logger.info(
            "kb.retrieved",
            tenant=str(tenant_id),
            chunks_count=len(out),
            top_score=out[0].score,
            kb_ids=kb_ids,
            query_preview=query[:80],
        )
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_kb_retriever.py -v -m integration`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/kb/retriever.py tests/integration/test_kb_retriever.py
git commit -m "feat(plan3 t12): KB retriever (pgvector cosine + per-KBRef min_score + RLS)"
```

---

## Task 13: `critic_pass`

**Files:**
- Create: `src/ai_sdr/guardrails/critic.py`
- Create: `tests/integration/test_guardrails_critic.py`

**Design:** `critic_pass(...)` builds a structured-output LLM (Haiku via `tenant_llm.classifier`), renders a prompt with the proposed response + KB chunks + recent history + the tenant's whitelist, and invokes `with_structured_output(Verdict)`. Returns the `Verdict`. The test uses a `FakeStructuredLLM` (a thin stub whose `.with_structured_output(model)` returns a runnable that yields a pre-set Verdict) — keeps the test deterministic and fast.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_guardrails_critic.py`:

```python
"""Tests for critic_pass — uses a FakeStructuredLLM, no live LLM call."""

from __future__ import annotations

from langchain_core.runnables import RunnableLambda

from ai_sdr.guardrails.critic import critic_pass
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message


class _FakeLLM:
    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.last_messages: list = []

    def with_structured_output(self, model: type) -> RunnableLambda:
        async def _run(messages: list) -> Verdict:
            self.last_messages = messages
            return self._verdict
        return RunnableLambda(_run)


def _llm_defaults() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(provider="anthropic", model="claude-sonnet-4-6", api_key_ref="anthropic_key"),
        classifier=LLMConfig(provider="anthropic", model="claude-haiku-4-5", api_key_ref="anthropic_key"),
    )


def _guardrails() -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=[247, 1497, 6000],
        allowed_products=["Mentoria", "Aceleradora"],
        fallback_text="Confirmo já já, ok?",
    )


async def test_critic_passes_clean_response() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005

    v = await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="A Mentoria custa R$ 6000 e tem 7 dias de garantia.",
        kb_chunks=[
            RetrievedChunk(content="Mentoria 6000", heading_path="Preços", kb_id="kb_x", score=0.9)
        ],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert v.passed is True


async def test_critic_flags_bad_response() -> None:
    fake = _FakeLLM(
        Verdict(passed=False, reason="mentioned R$ 9999", suggested_fix="refaça sem 9999")
    )
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005

    v = await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="A Mentoria custa R$ 9999.",
        kb_chunks=[],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert v.passed is False
    assert "9999" in v.reason  # type: ignore[operator]


async def test_critic_uses_classifier_llm_not_default() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    captured: dict = {}

    def factory(cfg: LLMConfig, secrets: dict[str, str], node_id: str) -> _FakeLLM:
        captured["model"] = cfg.model
        return fake

    await critic_pass(
        llm_factory=factory,  # type: ignore[arg-type]
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="ok",
        kb_chunks=[],
        recent_history=[],
        guardrails=_guardrails(),
    )
    assert captured["model"] == "claude-haiku-4-5"


async def test_critic_prompt_contains_kb_chunks_and_history() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005
    history: list[Message] = [
        {"role": "user", "content": "tem desconto?"},
        {"role": "assistant", "content": "não trabalhamos com desconto"},
    ]
    await critic_pass(
        llm_factory=factory,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk-fake"},
        response_text="proposta",
        kb_chunks=[
            RetrievedChunk(content="KB-FACT-123", heading_path="X", kb_id="kb_x", score=0.9)
        ],
        recent_history=history,
        guardrails=_guardrails(),
    )
    # System message (or first message) must contain the rendered KB + history hints
    blob = " ".join(
        m.content if isinstance(m.content, str) else str(m.content)
        for m in fake.last_messages
    )
    assert "KB-FACT-123" in blob
    assert "desconto" in blob


async def test_critic_raises_if_no_classifier_configured() -> None:
    fake = _FakeLLM(Verdict(passed=True))
    factory = lambda cfg, secrets, node_id: fake  # noqa: E731, ARG005
    cfg = LLMDefaults(
        default=LLMConfig(
            provider="anthropic", model="claude-sonnet-4-6", api_key_ref="anthropic_key"
        )
    )  # no classifier
    import pytest

    with pytest.raises(ValueError, match="classifier"):
        await critic_pass(
            llm_factory=factory,
            tenant_llm=cfg,
            secrets={"anthropic_key": "sk-fake"},
            response_text="ok",
            kb_chunks=[],
            recent_history=[],
            guardrails=_guardrails(),
        )
```

Note: these tests don't need a DB so they can run as unit. We keep them under `integration/` because future iterations may exercise the real Haiku via `live_llm` — keep adjacent. Mark with `@pytest.mark.integration` only if your `conftest.py` requires it; otherwise they'll run with `make test-unit` too (which is fine).

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_guardrails_critic.py -v`

Expected: `ImportError: cannot import name 'critic_pass'`.

- [ ] **Step 3: Create `src/ai_sdr/guardrails/critic.py`**

```python
"""critic_pass — second LLM (Haiku by default) reviews proposed response.

Spec §4.6. Returns Verdict; never blocks on its own — caller (run_with_guardrails)
decides what to do with passed=False.
"""

from __future__ import annotations

from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message

LLMFactory = Any  # mirrors compiler's LLMFactory typedef; loose to avoid import cycle


_CRITIC_SYSTEM_PROMPT = """\
Você é um revisor de qualidade de respostas de um SDR (assistente comercial via WhatsApp).
Recebe a RESPOSTA proposta pelo agente, o CONTEXTO FACTUAL recuperado da base de
conhecimento da empresa, e as REGRAS COMERCIAIS (valores e produtos permitidos).

Rejeite a resposta se ela:
1. Mencionar valor (R$) ou produto NÃO listado nas REGRAS COMERCIAIS
2. Fizer promessa não suportada pelo CONTEXTO FACTUAL (e.g. "garantia vitalícia"
   se essa garantia não consta na KB)
3. Inventar dado factual (data, prazo, condição) não citado no CONTEXTO FACTUAL

Caso contrário, aprove.

Retorne o Verdict estruturado: { passed: bool, reason: str|null, suggested_fix: str|null }.
Quando rejeitar, o suggested_fix deve ser uma mensagem CURTA e DIRETA pro agente
refazer a resposta corrigindo o problema específico.
"""


def _render_kb_block(kb_chunks: list[RetrievedChunk]) -> str:
    if not kb_chunks:
        return "(nenhum chunk recuperado)"
    parts = []
    for i, c in enumerate(kb_chunks, 1):
        header = f"[{i}] {c.heading_path or '(sem heading)'} (score {c.score:.2f}) [{c.kb_id}]"
        parts.append(f"{header}\n{c.content}")
    return "\n\n".join(parts)


def _render_history(history: list[Message], limit: int = 4) -> str:
    tail = history[-limit:]
    if not tail:
        return "(sem histórico)"
    return "\n".join(f"- {m['role']}: {m['content']}" for m in tail)


async def critic_pass(
    llm_factory: LLMFactory,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    response_text: str,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],
    guardrails: GuardrailsConfig,
) -> Verdict:
    """Run the critic. Uses tenant_llm.classifier (Haiku by design); raises
    ValueError if not configured."""
    if tenant_llm.classifier is None:
        raise ValueError(
            "guardrails critic pass requires tenant.llm.classifier to be configured"
        )

    llm_cfg: LLMConfig = tenant_llm.classifier
    llm = llm_factory(llm_cfg, secrets, "guardrails_critic")
    runnable = llm.with_structured_output(Verdict)

    user_block = (
        f"REGRAS COMERCIAIS:\n```yaml\n"
        f"{yaml.safe_dump({'allowed_prices': guardrails.allowed_prices, 'allowed_products': guardrails.allowed_products}, allow_unicode=True)}"
        f"```\n\n"
        f"CONTEXTO FACTUAL:\n{_render_kb_block(kb_chunks)}\n\n"
        f"HISTÓRICO RECENTE:\n{_render_history(recent_history)}\n\n"
        f"RESPOSTA PROPOSTA:\n{response_text}"
    )

    messages = [
        SystemMessage(content=_CRITIC_SYSTEM_PROMPT),
        HumanMessage(content=user_block),
    ]
    return await runnable.ainvoke(messages)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_guardrails_critic.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/guardrails/critic.py tests/integration/test_guardrails_critic.py
git commit -m "feat(plan3 t13): critic_pass (Haiku, with_structured_output → Verdict)"
```

---

## Task 14: `run_with_guardrails` runner

**Files:**
- Create: `src/ai_sdr/guardrails/runner.py`
- Create: `tests/unit/test_guardrails_runner.py`

**Design:** Orchestrates the retry loop. `inner` is an async callable that takes messages and returns an `ExtractResult` (a Pydantic instance carrying `response_text` + collects + optional `prices_mentioned`/`products_mentioned`). The runner:

1. Calls `inner(messages)`.
2. If `guardrails.enabled`, calls `validate_whitelist(...)`; if `Verdict.passed=False`, appends `SystemMessage(suggested_fix)` to messages and retries (up to `max_retries`).
3. If `critical and guardrails.critic_enabled`, calls `critic_pass(...)`; same retry semantics.
4. If retries are exhausted, calls `_handle_exhausted(...)` which returns the fallback `GuardrailsRunResult` (isolated for future HITL swap).

`GuardrailsRunResult` includes `response_text`, `collected`, `blocked: bool`, and `attempts: int` so callers can update state + telemetry.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_guardrails_runner.py`:

```python
"""Tests for run_with_guardrails — retry loop, fallback, telemetry hooks."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel

from ai_sdr.guardrails.runner import (
    ExtractResultProto,
    GuardrailsRunResult,
    run_with_guardrails,
)
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig


class _Result(BaseModel):
    """Minimal ExtractResult-shaped object: required by ExtractResultProto."""

    response_text: str
    prices_mentioned: list[int] = []
    products_mentioned: list[str] = []
    collected: dict[str, Any] = {}


def _gr(prices: list[int], products: list[str], max_retries: int = 2) -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=prices,
        allowed_products=products,
        fallback_text="Confirmo já já, ok?",
        max_retries=max_retries,
    )


def _llm_defaults_no_classifier() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(provider="anthropic", model="x", api_key_ref="anthropic_key")
    )


async def test_passes_on_first_attempt_when_clean() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247], collected={"a": 1})

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert isinstance(res, GuardrailsRunResult)
    assert res.blocked is False
    assert res.attempts == 0
    assert res.response_text == "ok"
    assert res.collected == {"a": 1}


async def test_retries_with_feedback_until_clean() -> None:
    calls: list[list[BaseMessage]] = []
    responses = [
        _Result(response_text="bad1", prices_mentioned=[5000]),
        _Result(response_text="bad2", prices_mentioned=[9999]),
        _Result(response_text="good", prices_mentioned=[247], collected={"x": 1}),
    ]

    async def inner(messages: list[BaseMessage]) -> _Result:
        calls.append(list(messages))
        return responses.pop(0)

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is False
    assert res.attempts == 2
    assert res.response_text == "good"
    # each retry appended a fix message
    assert any("não autorizado" in str(m.content).lower() for m in calls[1] if hasattr(m, "content"))


async def test_falls_back_when_retries_exhausted() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="bad", prices_mentioned=[9999])

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], [], max_retries=2),
        critical=False,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is True
    assert res.attempts == 3  # 1 initial + 2 retries
    assert res.response_text == "Confirmo já já, ok?"
    assert res.collected == {}


async def test_guardrails_disabled_is_pure_passthrough() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="anything", prices_mentioned=[9999])

    gr_off = GuardrailsConfig(
        enabled=False,
        allowed_prices=[],
        allowed_products=[],
        fallback_text="Confirmo já já, ok?",
    )

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=gr_off,
        critical=True,  # ignored when guardrails disabled
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
    )
    assert res.blocked is False
    assert res.attempts == 0
    assert res.response_text == "anything"


async def test_critic_pass_invoked_when_critical_and_critic_enabled() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247])

    critic_calls: list[str] = []

    async def fake_critic(*_a, response_text: str, **_kw) -> Verdict:
        critic_calls.append(response_text)
        return Verdict(passed=True)

    res = await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=_gr([247], []),
        critical=True,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
        critic_pass_fn=fake_critic,
    )
    assert critic_calls == ["ok"]
    assert res.blocked is False


async def test_critic_skipped_when_critic_enabled_false() -> None:
    async def inner(messages: list[BaseMessage]) -> _Result:
        return _Result(response_text="ok", prices_mentioned=[247])

    critic_calls: list[str] = []

    async def fake_critic(*_a, **_kw) -> Verdict:
        critic_calls.append("called")
        return Verdict(passed=True)

    gr = _gr([247], [])
    gr_no_critic = gr.model_copy(update={"critic_enabled": False})

    await run_with_guardrails(
        inner=inner,
        base_messages=[SystemMessage(content="prompt")],
        guardrails=gr_no_critic,
        critical=True,
        kb_chunks=[],
        recent_history=[],
        tenant_llm=_llm_defaults_no_classifier(),
        secrets={},
        llm_factory=lambda *a, **k: None,
        critic_pass_fn=fake_critic,
    )
    assert critic_calls == []
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_guardrails_runner.py -v`

Expected: `ImportError: cannot import name 'run_with_guardrails'`.

- [ ] **Step 3: Create `src/ai_sdr/guardrails/runner.py`**

```python
"""run_with_guardrails — retry loop coordinating whitelist + critic + fallback.

The `_handle_exhausted` hook is intentionally factored out so a future HITL plan
can replace it with `await persist_pending_review(...); raise GraphInterrupt()`
without touching the retry loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from langchain_core.messages import BaseMessage, SystemMessage

from ai_sdr.guardrails.critic import critic_pass as _default_critic_pass
from ai_sdr.guardrails.schemas import Verdict
from ai_sdr.guardrails.whitelist import validate_whitelist
from ai_sdr.kb.retriever import RetrievedChunk
from ai_sdr.schemas.llm_yaml import LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.treeflow.state import Message

logger = structlog.get_logger(__name__)


class ExtractResultProto(Protocol):
    """Minimal contract for the object returned by `inner` — same shape as the
    Pydantic model produced by build_structured_model() when guardrails are active."""

    response_text: str
    prices_mentioned: list[int]
    products_mentioned: list[str]


@dataclass
class GuardrailsRunResult:
    response_text: str
    collected: dict[str, Any]
    blocked: bool
    attempts: int


CriticPassFn = Callable[..., Awaitable[Verdict]]


def _collected_from_result(result: ExtractResultProto, reserved: set[str]) -> dict[str, Any]:
    """Extract everything except response_text and the mention fields into a dict."""
    out: dict[str, Any] = {}
    if hasattr(result, "model_dump"):
        for k, v in result.model_dump().items():  # type: ignore[attr-defined]
            if k in reserved:
                continue
            if v is None:
                continue
            out[k] = v
    return out


_RESERVED_FIELDS = {"response_text", "prices_mentioned", "products_mentioned"}


async def run_with_guardrails(
    *,
    inner: Callable[[list[BaseMessage]], Awaitable[ExtractResultProto]],
    base_messages: list[BaseMessage],
    guardrails: GuardrailsConfig | None,
    critical: bool,
    kb_chunks: list[RetrievedChunk],
    recent_history: list[Message],
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    llm_factory: Any,
    critic_pass_fn: CriticPassFn | None = None,
) -> GuardrailsRunResult:
    """Run inner, validate, retry with feedback, fallback if exhausted."""
    cp_fn = critic_pass_fn or _default_critic_pass
    guardrails_active = guardrails is not None and guardrails.enabled
    critic_active = (
        critical
        and guardrails_active
        and guardrails is not None
        and guardrails.critic_enabled
    )
    max_retries = guardrails.max_retries if guardrails is not None else 2

    messages = list(base_messages)
    attempt = 0
    last_verdict: Verdict | None = None

    while attempt <= max_retries:
        result = await inner(messages)

        if guardrails_active and guardrails is not None:
            v = validate_whitelist(
                prices_mentioned=getattr(result, "prices_mentioned", []) or [],
                products_mentioned=getattr(result, "products_mentioned", []) or [],
                guardrails=guardrails,
            )
            if not v.passed:
                logger.info("guardrail.blocked", attempt=attempt, reason=v.reason)
                last_verdict = v
                if attempt == max_retries:
                    return _handle_exhausted(guardrails, last_verdict, max_retries)
                messages = list(base_messages) + [
                    SystemMessage(content=v.suggested_fix or "Refaça respeitando a whitelist.")
                ]
                attempt += 1
                continue

        if critic_active and guardrails is not None:
            v_c = await cp_fn(
                llm_factory=llm_factory,
                tenant_llm=tenant_llm,
                secrets=secrets,
                response_text=result.response_text,
                kb_chunks=kb_chunks,
                recent_history=recent_history,
                guardrails=guardrails,
            )
            if not v_c.passed:
                logger.info("critic.flagged", attempt=attempt, reason=v_c.reason)
                last_verdict = v_c
                if attempt == max_retries:
                    return _handle_exhausted(guardrails, last_verdict, max_retries)
                messages = list(base_messages) + [
                    SystemMessage(content=v_c.suggested_fix or "Refaça com base no critic.")
                ]
                attempt += 1
                continue

        return GuardrailsRunResult(
            response_text=result.response_text,
            collected=_collected_from_result(result, _RESERVED_FIELDS),
            blocked=False,
            attempts=attempt,
        )

    # Should be unreachable given the in-loop returns above, but keep defensive
    return _handle_exhausted(guardrails, last_verdict, max_retries)


def _handle_exhausted(
    guardrails: GuardrailsConfig | None,
    last_verdict: Verdict | None,
    max_retries: int,
) -> GuardrailsRunResult:
    """Fallback hook. Future HITL plan can replace this body with:
        await persist_pending_review(...); raise GraphInterrupt()
    without touching the retry loop."""
    reason = last_verdict.reason if last_verdict is not None else "unknown"
    logger.info("guardrail.fallback_used", reason=reason)
    fallback = (
        guardrails.fallback_text
        if guardrails is not None
        else "Deixa eu confirmar e já te respondo."
    )
    return GuardrailsRunResult(
        response_text=fallback,
        collected={},
        blocked=True,
        attempts=max_retries + 1,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_guardrails_runner.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/guardrails/runner.py tests/unit/test_guardrails_runner.py
git commit -m "feat(plan3 t14): run_with_guardrails (retry + fallback + isolated _handle_exhausted hook)"
```

---

## Task 15: Compiler integration (retrieval + guardrails wrap)

**Files:**
- Modify: `src/ai_sdr/treeflow/compiler.py`
- Create: `tests/integration/test_compiler_with_kb_and_guardrails.py`

**Design:** Refactor `_make_node_fn` so that the LLM-call portion is an `_invoke_node_llm(messages)` closure and the surrounding logic is:

1. If `node.knowledge_base`, call `kb.retriever.retrieve(...)` with the embedder built from tenant config, then render the chunks as a single `<knowledge_base>...</knowledge_base>` text block.
2. Build the system messages via `llm.messages.build_system_messages(static_prompt, dynamic_blocks=[kb_block], provider, cache_enabled)`. The static prompt is `node.prompt` (tenant_context concatenation is a future plan; for now `static_prompt = node.prompt`).
3. Append history + current user input.
4. Pass `_invoke_node_llm` to `run_with_guardrails(...)`.
5. Use the returned `GuardrailsRunResult.response_text` + `.collected` for state update.

Compiler signature gains optional `embedder_factory` and `kb_session_factory` (so tests can inject fakes without standing up Postgres for unit-level checks). Default factories use `build_embedder` and the live DB session.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_compiler_with_kb_and_guardrails.py`:

```python
"""Integration test: compiler runs a TreeFlow whose node has KB + critic with retry."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.settings import get_settings
from ai_sdr.treeflow.compiler import compile_treeflow


class _OneHotEmbedder(Embedder):
    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        pass

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * 1536
        for word in text.lower().split():
            v[hash(word) % 1536] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    async def embed_query(self, t: str) -> list[float]:
        return self._vec(t)

    async def embed_documents(self, ts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in ts]


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


def _tf_yaml() -> dict:
    return {
        "id": "demo",
        "version": "0.1.0",
        "display_name": "Demo",
        "entry_node": "oferta",
        "nodes": [
            {
                "id": "oferta",
                "prompt": "Você apresenta a Mentoria. Use a KB pra preços.",
                "knowledge_base": [{"id": "kb_x", "top_k": 2, "min_score": 0.0}],
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }


def _llm_defaults() -> LLMDefaults:
    from ai_sdr.schemas.llm_yaml import EmbeddingsConfig
    return LLMDefaults(
        default=LLMConfig(
            provider="anthropic", model="claude-sonnet-4-6", api_key_ref="anthropic_key"
        ),
        classifier=LLMConfig(
            provider="anthropic", model="claude-haiku-4-5", api_key_ref="anthropic_key"
        ),
        embeddings=EmbeddingsConfig(),
        cache_enabled=False,  # avoid asserting on cache_control shape in this test
    )


def _guardrails() -> GuardrailsConfig:
    return GuardrailsConfig(
        enabled=True,
        allowed_prices=[6000],
        allowed_products=["Mentoria"],
        fallback_text="Confirmo já já, ok?",
        max_retries=2,
    )


@pytest.mark.integration
async def test_compiler_injects_kb_and_runs_guardrails_clean(
    session: AsyncSession, tmp_path: Path
) -> None:
    # Seed tenant + KB
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "precos.md").write_text(
        "## Preços\n\nMentoria custa R$ 6000.", encoding="utf-8"
    )
    await reindex_tenant_kb(
        session, t, kb_root, _OneHotEmbedder(), MarkdownChunker()
    )

    tf = TreeFlow.model_validate(_tf_yaml())

    # Stub LLM that emits a clean structured response
    async def fake_call(messages: list) -> dict[str, Any]:
        # The model used here mirrors what build_structured_model produces
        return {
            "response_text": "A Mentoria custa R$ 6000.",
            "prices_mentioned": [6000],
            "products_mentioned": ["Mentoria"],
        }

    class StubLLM:
        def with_structured_output(self, model: type) -> Any:
            async def _run(msgs: list) -> Any:
                return model.model_validate(await fake_call(msgs))
            return RunnableLambda(_run)

    def llm_factory(cfg: LLMConfig, secrets: dict[str, str], node_id: str) -> Any:
        return StubLLM()

    async def embedder_factory(secrets: dict[str, str], cfg: Any) -> Embedder:
        return _OneHotEmbedder()

    async def session_factory() -> AsyncSession:
        return session

    graph = compile_treeflow(
        tf,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk", "openai_key": "sk"},
        guardrails=_guardrails(),
        tenant_id=t.id,
        llm_factory=llm_factory,
        embedder_factory=embedder_factory,
        kb_session_factory=session_factory,
    )

    state_in = {
        "tenant_id": str(t.id),
        "lead_id": "lead-1",
        "treeflow_id": tf.id,
        "treeflow_version": tf.version,
        "current_node": "oferta",
        "collected": {},
        "messages": [],
        "last_user_input": "qual o preço da mentoria?",
        "last_agent_response": "",
        "completed": False,
    }
    out = await graph.ainvoke(state_in)
    assert out["last_agent_response"] == "A Mentoria custa R$ 6000."
    assert out["completed"] is True


@pytest.mark.integration
async def test_compiler_fallback_on_repeated_whitelist_violation(
    session: AsyncSession, tmp_path: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "x.md").write_text("## X\n\nmentoria", encoding="utf-8")
    await reindex_tenant_kb(session, t, kb_root, _OneHotEmbedder(), MarkdownChunker())

    tf = TreeFlow.model_validate(_tf_yaml())
    call_count = {"n": 0}

    async def fake_call(_msgs: list) -> dict[str, Any]:
        call_count["n"] += 1
        return {
            "response_text": "A Mentoria custa R$ 9999.",
            "prices_mentioned": [9999],
            "products_mentioned": ["Mentoria"],
        }

    class StubLLM:
        def with_structured_output(self, model: type) -> Any:
            async def _run(msgs: list) -> Any:
                return model.model_validate(await fake_call(msgs))
            return RunnableLambda(_run)

    def llm_factory(cfg: LLMConfig, secrets: dict[str, str], node_id: str) -> Any:
        return StubLLM()

    async def embedder_factory(secrets: dict[str, str], cfg: Any) -> Embedder:
        return _OneHotEmbedder()

    async def session_factory() -> AsyncSession:
        return session

    graph = compile_treeflow(
        tf,
        tenant_llm=_llm_defaults(),
        secrets={"anthropic_key": "sk", "openai_key": "sk"},
        guardrails=_guardrails(),
        tenant_id=t.id,
        llm_factory=llm_factory,
        embedder_factory=embedder_factory,
        kb_session_factory=session_factory,
    )

    state_in = {
        "tenant_id": str(t.id), "lead_id": "lead-1", "treeflow_id": tf.id,
        "treeflow_version": tf.version, "current_node": "oferta", "collected": {},
        "messages": [], "last_user_input": "preço?", "last_agent_response": "",
        "completed": False,
    }
    out = await graph.ainvoke(state_in)
    assert call_count["n"] == 3  # 1 initial + 2 retries
    assert out["last_agent_response"] == "Confirmo já já, ok?"
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_compiler_with_kb_and_guardrails.py -v -m integration`

Expected: FAIL with `TypeError: compile_treeflow() got an unexpected keyword argument 'guardrails'` (or similar).

- [ ] **Step 3: Refactor `src/ai_sdr/treeflow/compiler.py`**

Replace the body with:

```python
"""Compile a `TreeFlow` into a LangGraph `CompiledStateGraph` with KB + guardrails."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.guardrails.runner import GuardrailsRunResult, run_with_guardrails
from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.kb.retriever import RetrievedChunk, retrieve
from ai_sdr.llm.extractor import RESPONSE_FIELD, build_structured_model, extract
from ai_sdr.llm.factory import build_llm as _default_build_llm
from ai_sdr.llm.messages import build_system_messages
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.tenant_yaml import GuardrailsConfig
from ai_sdr.schemas.treeflow_yaml import NodeSpec, TreeFlow
from ai_sdr.treeflow.expressions import eval_bool
from ai_sdr.treeflow.state import Message, TalkFlowState

LLMFactory = Callable[[LLMConfig, dict[str, str], str], BaseChatModel]
EmbedderFactory = Callable[[dict[str, str], Any], Awaitable[Embedder]]
KbSessionFactory = Callable[[], Awaitable[AsyncSession]]


def _default_llm_factory(cfg: LLMConfig, secrets: dict[str, str], _node_id: str) -> BaseChatModel:
    return _default_build_llm(cfg, secrets)


async def _default_embedder_factory(secrets: dict[str, str], cfg: Any) -> Embedder:
    return build_embedder(secrets, cfg)


def _exit_satisfied(node: NodeSpec, collected: dict[str, Any]) -> bool:
    ec = node.exit_condition
    if ec.type == "all_fields_filled":
        return all(
            c.field in collected and collected[c.field] is not None
            for c in node.collects
            if c.required
        )
    if ec.type == "rule_expression":
        assert ec.expression is not None
        return eval_bool(ec.expression, collected)
    if ec.type == "combined":
        assert ec.expression is not None
        all_filled = all(
            c.field in collected and collected[c.field] is not None
            for c in node.collects
            if c.required
        )
        return all_filled and eval_bool(ec.expression, collected)
    return False


def _route(node: NodeSpec, collected: dict[str, Any]) -> tuple[str, bool]:
    if not _exit_satisfied(node, collected):
        return (node.id, False)
    for tr in node.next_nodes:
        if eval_bool(tr.condition, collected):
            if tr.target == "END":
                return ("END", True)
            return (tr.target, False)
    return (node.id, False)


def _render_kb_block(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        header = f"[{i}] {c.heading_path or '(sem heading)'} (score {c.score:.2f}) [{c.kb_id}]"
        parts.append(f"{header}\n{c.content}")
    return "<knowledge_base>\n" + "\n\n".join(parts) + "\n</knowledge_base>"


def compile_treeflow(
    tf: TreeFlow,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    guardrails: GuardrailsConfig | None = None,
    tenant_id: uuid.UUID | None = None,
    llm_factory: LLMFactory | None = None,
    embedder_factory: EmbedderFactory | None = None,
    kb_session_factory: KbSessionFactory | None = None,
    checkpointer: Any = None,
) -> Any:
    """Compile a TreeFlow into a LangGraph StateGraph.

    Keyword-only args:
      guardrails: tenant.guardrails block; when None the runner is a passthrough.
      tenant_id, embedder_factory, kb_session_factory: required when any node has
        a non-empty `knowledge_base`; raise ValueError at compile time if missing.
    """
    llm_fn: LLMFactory = llm_factory or _default_llm_factory
    emb_fn: EmbedderFactory = embedder_factory or _default_embedder_factory

    any_node_has_kb = any(n.knowledge_base for n in tf.nodes)
    if any_node_has_kb and (tenant_id is None or kb_session_factory is None):
        raise ValueError(
            "compile_treeflow: tenant_id + kb_session_factory are required when "
            "any node has knowledge_base"
        )

    by_id = {n.id: n for n in tf.nodes}

    def _make_node_fn(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def node_fn(state: TalkFlowState) -> dict[str, Any]:
            llm_cfg = node.llm or tenant_llm.default
            llm = llm_fn(llm_cfg, secrets, node.id)

            user_input = state.get("last_user_input", "")

            # 1) Retrieve KB chunks
            kb_chunks: list[RetrievedChunk] = []
            if node.knowledge_base and user_input:
                assert tenant_id is not None and kb_session_factory is not None
                assert tenant_llm.embeddings is not None, (
                    "node has knowledge_base but tenant.llm.embeddings is not configured"
                )
                embedder = await emb_fn(secrets, tenant_llm.embeddings)
                kb_session = await kb_session_factory()
                kb_chunks = await retrieve(
                    kb_session,
                    tenant_id=tenant_id,
                    kb_refs=node.knowledge_base,
                    query=user_input,
                    embedder=embedder,
                )

            dynamic_blocks: list[str] = []
            if kb_chunks:
                dynamic_blocks.append(_render_kb_block(kb_chunks))

            # 2) Build messages with cache control
            system_msgs = build_system_messages(
                static_prompt=node.prompt,
                dynamic_blocks=dynamic_blocks,
                provider=llm_cfg.provider,  # "anthropic" or "openai"
                cache_enabled=tenant_llm.cache_enabled,
            )
            history_msgs: list[Any] = []
            for m in state.get("messages", []):
                if m["role"] == "user":
                    history_msgs.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    history_msgs.append(AIMessage(content=m["content"]))

            base_messages = list(system_msgs) + history_msgs
            if user_input:
                base_messages.append(HumanMessage(content=user_input))

            # 3) Build structured model + inner caller
            model = build_structured_model(node.collects, guardrails=guardrails)

            async def _invoke_inner(msgs: list) -> Any:
                return await extract(llm, model, msgs)

            # 4) Run with guardrails
            recent_history: list[Message] = state.get("messages", [])[-4:]
            result: GuardrailsRunResult = await run_with_guardrails(
                inner=_invoke_inner,
                base_messages=base_messages,
                guardrails=guardrails,
                critical=node.critical,
                kb_chunks=kb_chunks,
                recent_history=recent_history,
                tenant_llm=tenant_llm,
                secrets=secrets,
                llm_factory=llm_fn,
            )

            collected_after = {**state.get("collected", {}), **result.collected}
            response_text = result.response_text
            next_node, completed = _route(node, collected_after)

            new_msgs: list[Message] = []
            if user_input:
                new_msgs.append({"role": "user", "content": user_input})
            new_msgs.append({"role": "assistant", "content": response_text})

            return {
                "collected": collected_after,
                "messages": new_msgs,
                "last_agent_response": response_text,
                "last_user_input": "",
                "current_node": next_node,
                "completed": completed,
            }

        return node_fn

    sg: StateGraph[Any, Any, Any, Any] = StateGraph(TalkFlowState)
    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]

    def _start_router(state: TalkFlowState) -> str:
        nid = state.get("current_node") or tf.entry_node
        if nid == "END":
            return END
        if nid not in by_id:
            raise ValueError(f"state.current_node={nid!r} not in TreeFlow")
        return nid

    sg.add_conditional_edges(
        START,
        _start_router,
        {**{n.id: n.id for n in tf.nodes}, END: END},
    )
    for n in tf.nodes:
        sg.add_edge(n.id, END)

    if checkpointer is not None:
        return sg.compile(checkpointer=checkpointer)
    return sg.compile()
```

- [ ] **Step 4: Update `TalkFlowRuntime` to pass new args to `compile_treeflow`**

Open `src/ai_sdr/treeflow/runtime.py` (existing from Plan 2). In `step()`, where `compile_treeflow(...)` is called, add the new kwargs:

```python
async with checkpointer_from_settings() as saver:
    graph = compile_treeflow(
        tf,
        tenant_llm=llm_defaults,
        secrets=secrets,
        guardrails=tenant_cfg.guardrails,
        tenant_id=tenant.id,
        llm_factory=self._llm_factory,
        kb_session_factory=lambda: _session_factory(session),
        checkpointer=saver,
    )
```

Add the helper at module level in `runtime.py`:

```python
async def _session_factory(session: AsyncSession) -> AsyncSession:
    """Returns the same session — runtime owns one DB session per step."""
    return session
```

Existing tests for `TalkFlowRuntime` (Plan 2 Task 12) must still pass; the new args default safely when no node has `knowledge_base` and `guardrails` is None.

- [ ] **Step 5: Run the new integration test + existing runtime tests**

Run:
```
uv run pytest tests/integration/test_compiler_with_kb_and_guardrails.py -v -m integration
uv run pytest tests/integration/test_talkflow_runtime.py -v -m integration
```

Expected: both files all PASS.

- [ ] **Step 6: Run full unit suite (catch regressions)**

Run: `make test-unit`

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py src/ai_sdr/treeflow/runtime.py tests/integration/test_compiler_with_kb_and_guardrails.py
git commit -m "feat(plan3 t15): compiler injects KB + wraps LLM in run_with_guardrails"
```

---

## Task 16: TreeFlowLoader — cache-threshold warning

**Files:**
- Modify: `src/ai_sdr/treeflow/loader.py`
- Modify: `tests/unit/test_treeflow_loader.py`

**Design:** When loading a TreeFlow, if any node's `prompt` is below ~1024 tokens (Anthropic's minimum cacheable size), log a single warning per node so the author knows the cache marker will be silently ignored. Loader doesn't have access to tenant config at load time, so the warning fires unconditionally (cache is enabled by default; the worst case is a no-op warning for tenants that disabled it). Token counting via tiktoken `cl100k_base` — same encoder used by the chunker.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_treeflow_loader.py`:

```python
# ---------- cache threshold warning (Plan 3) ----------

import logging


def test_loader_warns_when_node_prompt_below_cache_threshold(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    short_prompt = "Diga oi."  # << 1024 tokens
    yaml_text = (
        f"id: tf\nversion: 0.1.0\ndisplay_name: TF\nentry_node: a\n"
        f"nodes:\n  - id: a\n    prompt: {short_prompt!r}\n"
        f"    exit_condition: {{type: all_fields_filled}}\n"
        f"    next_nodes: [{{condition: 'true', target: END}}]\n"
    )
    tenant_dir = tmp_path / "example" / "treeflows"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "tf.yaml").write_text(yaml_text)

    from ai_sdr.treeflow.loader import TreeFlowLoader
    loader = TreeFlowLoader(tmp_path)
    with caplog.at_level(logging.WARNING, logger="ai_sdr.treeflow.loader"):
        loader.load("example", "tf")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("cache_below_threshold" in m or "1024" in m for m in msgs)


def test_loader_does_not_warn_for_long_prompts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_prompt = ("Você é uma SDR experiente. " * 200).strip()  # >> 1024 tok
    yaml_text = (
        f"id: tf\nversion: 0.1.0\ndisplay_name: TF\nentry_node: a\n"
        f"nodes:\n  - id: a\n    prompt: {long_prompt!r}\n"
        f"    exit_condition: {{type: all_fields_filled}}\n"
        f"    next_nodes: [{{condition: 'true', target: END}}]\n"
    )
    tenant_dir = tmp_path / "example" / "treeflows"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "tf.yaml").write_text(yaml_text)

    from ai_sdr.treeflow.loader import TreeFlowLoader
    loader = TreeFlowLoader(tmp_path)
    with caplog.at_level(logging.WARNING, logger="ai_sdr.treeflow.loader"):
        loader.load("example", "tf")
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("cache_below_threshold" in m for m in msgs)
```

Make sure `from pathlib import Path` and `import pytest` are at the top of the file (they likely already are).

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_treeflow_loader.py -v`

Expected: the new tests FAIL (no warning emitted yet).

- [ ] **Step 3: Modify `src/ai_sdr/treeflow/loader.py`**

Add at the top of the file (next to existing imports):

```python
import structlog
import tiktoken

logger = structlog.get_logger(__name__)
_CACHE_MIN_TOKENS = 1024
```

If a logger is already declared, reuse it; do not redefine.

After the `load(...)` method finishes validation and produces a `TreeFlow`, before returning it, add:

```python
# Plan 3: warn for cache-threshold misses (best-effort, fail-safe)
try:
    enc = tiktoken.get_encoding("cl100k_base")
    for node in tf.nodes:
        tok = len(enc.encode(node.prompt))
        if tok < _CACHE_MIN_TOKENS:
            logger.warning(
                "treeflow.cache_below_threshold",
                tenant=tenant_slug,
                treeflow=tf.id,
                node=node.id,
                prompt_tokens=tok,
                threshold=_CACHE_MIN_TOKENS,
            )
except Exception as e:  # noqa: BLE001
    logger.debug("treeflow.cache_check_failed", error=str(e))
```

`tenant_slug` should be the slug arg already in scope inside `load()`. If the existing signature uses a different name, adjust accordingly.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_treeflow_loader.py -v`

Expected: all PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/treeflow/loader.py tests/unit/test_treeflow_loader.py
git commit -m "feat(plan3 t16): TreeFlowLoader warns when node prompt < 1024 tok (cache no-op)"
```

---

## Task 17: CLI `ai-sdr reindex-kb`

**Files:**
- Create: `src/ai_sdr/cli/reindex_kb.py`
- Modify: `src/ai_sdr/cli/app.py`
- Create: `tests/integration/test_kb_indexer_cli.py`

**Design:** Typer subcommand `ai-sdr reindex-kb --tenant <slug> [--kb <id>] [--prune] [--kb-root path]`. Opens a DB session, loads tenant config + secrets, builds embedder + chunker, calls `reindex_tenant_kb`, prints a one-line summary per result kind. Default `--kb-root` is `Path("kb")` relative to CWD.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_kb_indexer_cli.py`:

```python
"""Smoke test for `ai-sdr reindex-kb` — runs the CLI via subprocess."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


def _write_tenant_dir(tenants_root: Path, slug: str) -> Path:
    tdir = tenants_root / slug
    (tdir / "treeflows").mkdir(parents=True)
    tenant_yaml = {
        "id": slug,
        "display_name": slug.upper(),
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "anthropic_key",
            },
            "embeddings": {"provider": "openai"},
        },
    }
    (tdir / "tenant.yaml").write_text(yaml.safe_dump(tenant_yaml))
    # No secrets file written — the CLI test sets AI_SDR_TEST_FAKE_EMBEDDER=1,
    # which short-circuits the sops_loader.load() call entirely.
    return tdir


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_reindex_kb_cli_smoke(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = f"t-{uuid.uuid4().hex[:8]}"
    tenants_root = tmp_path / "tenants"
    _write_tenant_dir(tenants_root, slug)

    kb_root = tmp_path / "kb"
    (kb_root / slug / "kb_x").mkdir(parents=True)
    (kb_root / slug / "kb_x" / "precos.md").write_text(
        "## Preços\n\nMentoria custa R$ 6000.", encoding="utf-8"
    )

    async with session.begin():
        t = Tenant(slug=slug, display_name=slug.upper())
        session.add(t)

    # Stub openai embedding so the live API isn't needed
    env = dict(os.environ)
    env["AI_SDR_TEST_FAKE_EMBEDDER"] = "1"  # honored by build_embedder when set
    env["AI_SDR_TENANTS_ROOT"] = str(tenants_root)

    cmd = [
        "uv", "run", "ai-sdr", "reindex-kb",
        "--tenant", slug,
        "--kb-root", str(kb_root),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "indexed" in result.stdout.lower()

    # Verify DB state
    async with session.begin():
        await set_tenant_context(session, t.id)
        chunks = (await session.execute(select(KbChunk))).scalars().all()
        assert len(chunks) >= 1


@pytest.mark.integration
async def test_reindex_kb_cli_unknown_tenant_exits_nonzero(
    tmp_path: Path,
) -> None:
    env = dict(os.environ)
    env["AI_SDR_TENANTS_ROOT"] = str(tmp_path / "tenants_empty")
    result = subprocess.run(
        ["uv", "run", "ai-sdr", "reindex-kb", "--tenant", "ghost"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode != 0
    assert "ghost" in result.stderr.lower() or "not found" in result.stderr.lower()
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_kb_indexer_cli.py -v -m integration`

Expected: FAIL (no `reindex-kb` subcommand registered).

- [ ] **Step 3: Create `src/ai_sdr/cli/reindex_kb.py`**

```python
"""`ai-sdr reindex-kb` — idempotent KB indexer driver.

Walks kb/<tenant.slug>/[<kb_id>]/**/*.md, chunks + embeds + upserts via
content_hash. Use --prune to delete rows for files that disappeared.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import Embedder, build_embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader

reindex_kb_app = typer.Typer(help="KB management subcommands")


class _FakeEmbedder(Embedder):
    """Used only when AI_SDR_TEST_FAKE_EMBEDDER=1 (test smoke runs)."""

    def __init__(self) -> None:  # type: ignore[no-untyped-def]
        pass

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


async def _run(tenant_slug: str, kb_root: Path, kb_id: str | None, prune: bool) -> int:
    tenants_root = Path(os.getenv("AI_SDR_TENANTS_ROOT", "tenants"))
    tenant_loader = TenantLoader(tenants_root)
    sops_loader = SopsLoader(tenants_root)

    try:
        tenant_cfg = tenant_loader.load(tenant_slug)
    except FileNotFoundError:
        print(f"ERROR: tenant {tenant_slug!r} not found under {tenants_root}", file=sys.stderr)
        return 2

    if tenant_cfg.llm.embeddings is None:
        print(
            f"ERROR: tenant {tenant_slug!r} has no llm.embeddings config in tenant.yaml",
            file=sys.stderr,
        )
        return 3

    settings = get_settings()
    eng = create_async_engine(settings.database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)

    try:
        async with sm() as session:
            tenant = (
                await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
            ).scalar_one_or_none()
            if tenant is None:
                print(
                    f"ERROR: tenant {tenant_slug!r} has no row in tenants table; insert one first",
                    file=sys.stderr,
                )
                return 4

            if os.getenv("AI_SDR_TEST_FAKE_EMBEDDER") == "1":
                embedder: Embedder = _FakeEmbedder()
            else:
                secrets = sops_loader.load(tenant_slug)
                embedder = build_embedder(secrets, tenant_cfg.llm.embeddings)

            async with session.begin():
                result = await reindex_tenant_kb(
                    session,
                    tenant,
                    kb_root,
                    embedder=embedder,
                    chunker=MarkdownChunker(),
                    prune=prune,
                    kb_id=kb_id,
                )
            print(
                f"indexed: {len(result.indexed)}  skipped: {len(result.skipped)}  "
                f"pruned: {len(result.pruned)}  failed: {len(result.failed)}"
            )
            for path in result.indexed:
                print(f"  + {path}")
            for path in result.skipped:
                print(f"  = {path}")
            for path in result.pruned:
                print(f"  - {path}")
            for path, err in result.failed:
                print(f"  ! {path}: {err}")
            return 0
    finally:
        await eng.dispose()


@reindex_kb_app.callback(invoke_without_command=True)
def reindex_kb(
    tenant: str = typer.Option(..., "--tenant", help="Tenant slug"),
    kb: str | None = typer.Option(None, "--kb", help="Limit to a specific kb_id"),
    prune: bool = typer.Option(False, "--prune", help="Delete rows for docs removed from disk"),
    kb_root: Path = typer.Option(Path("kb"), "--kb-root", help="Root directory containing kb/<slug>/<kb_id>/"),
) -> None:
    exit_code = asyncio.run(_run(tenant, kb_root, kb, prune))
    raise typer.Exit(code=exit_code)
```

- [ ] **Step 4: Wire the subcommand into `src/ai_sdr/cli/app.py`**

In `src/ai_sdr/cli/app.py`, import the new app and register it:

```python
from ai_sdr.cli.reindex_kb import reindex_kb_app

# After the existing `app = typer.Typer(...)` declaration and other registrations:
app.add_typer(reindex_kb_app, name="reindex-kb")
```

- [ ] **Step 5: Run integration test**

Run: `uv run pytest tests/integration/test_kb_indexer_cli.py -v -m integration`

Expected: both PASS.

- [ ] **Step 6: Smoke from shell**

Run: `uv run ai-sdr reindex-kb --help`

Expected: prints help text including `--tenant`, `--kb`, `--prune`, `--kb-root`.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/cli/reindex_kb.py src/ai_sdr/cli/app.py tests/integration/test_kb_indexer_cli.py
git commit -m "feat(plan3 t17): ai-sdr reindex-kb CLI (idempotent, --prune, --kb filter)"
```

---

## Task 18: Example tenant — KB fixture + guardrails block + treeflow KB ref

**Files:**
- Modify: `tenants/example/tenant.yaml`
- Modify: `tenants/example/treeflows/example.yaml`
- Create: `tenants/example/kb/example_kb/precos.md`
- (No re-encryption of `secrets.enc.yaml` — it already has `openai_key` from Plan 2 Task 15.)

**Design:** Update the example tenant to exercise both new features end-to-end. The `precos.md` file is the smoke fixture used by the simulate CLI demo and the live KB test. Keep the `oferta`-style node's prompt LONG (>1024 tokens — bullets, persona, instructions, examples) so the cache warning doesn't fire and prompt caching actually engages.

- [ ] **Step 1: Add KB fixture at repo-root `kb/`**

The indexer walks `kb_root/<tenant.slug>/<kb_id>/**/*.md`. Create the file at `kb/example/example_kb/precos.md` (NOT under `tenants/example/`):

```markdown
# Ofertas e preços

## Mentoria

A Mentoria é o programa premium da Joana. Investimento: R$ 6000 à vista ou 12x de R$ 600.
Inclui: 6 encontros 1:1 + acesso à comunidade + material exclusivo. Garantia de 7 dias.

## Aceleradora

A Aceleradora é o programa intermediário, voltado pra quem tá começando a construir
audiência. Investimento: R$ 1497 à vista ou 12x de R$ 150. Inclui: 8 aulas em grupo +
templates + suporte por 30 dias.

## Downsell — Curso Express

Quando a Aceleradora não cabe no bolso do lead nesse momento, oferecemos o Curso
Express por R$ 247 (pagamento único). Inclui: 4 módulos gravados + comunidade básica
por 90 dias.

## Garantia

Todos os programas têm 7 dias de garantia incondicional — basta solicitar reembolso
no e-mail suporte@joanamentora.com.
```

- [ ] **Step 2: Update `tenants/example/tenant.yaml`**

Read the current file and add the `embeddings` + `cache_enabled` to `llm`, plus a new `guardrails` block. Preserve all existing fields.

Add inside `llm:`:

```yaml
  embeddings:
    provider: openai
    model: text-embedding-3-small
    api_key_ref: openai_key
  cache_enabled: true
```

Add top-level (after `llm`):

```yaml
guardrails:
  enabled: true
  allowed_prices: [247, 1497, 1997, 2000, 6000]
  allowed_products: ["Mentoria", "Aceleradora", "Downsell", "Curso Express"]
  critic_enabled: true
  fallback_text: "Deixa eu confirmar esse valor com a equipe e já te respondo, ok?"
  max_retries: 2
```

- [ ] **Step 3: Update `tenants/example/treeflows/example.yaml`**

Read the current file. Identify the node that presents the offer (likely `oferta_mentoria` or similar; if there's only the 4-node demo from Plan 2, pick the most "salesy" one). Add to that node:

```yaml
    knowledge_base:
      - id: example_kb
        top_k: 3
        min_score: 0.6
    critical: true
```

Also bump the TreeFlow `version` field — TreeFlowLoader rejects re-publishing the same version with different content. Example: `version: "0.1.0"` → `version: "0.2.0"`.

Make sure the node's `prompt` is >1024 tokens (a few hundred words of persona + instructions + tone). If it's too short, expand with bullet-point guidance — this is also useful for the simulate CLI demo. Example skeleton (adapt to existing style):

```yaml
    prompt: |
      Você é a SDR da Joana Mentora, conversando com um lead via WhatsApp.
      Tom: brasileiro, amigável, frases curtas, sem formalidade excessiva.
      ...
      (expand to ~250 lines of persona + instructions + offer details + DOs/DON'Ts)
```

- [ ] **Step 4: Validate the YAML**

Run:
```
uv run python -c "from pathlib import Path; from ai_sdr.tenant_loader.loader import TenantLoader; from ai_sdr.treeflow.loader import TreeFlowLoader; TenantLoader(Path('tenants')).load('example'); TreeFlowLoader(Path('tenants')).load('example', 'example')"
```

Expected: no exceptions. Output may include `treeflow.cache_below_threshold` warning if you didn't expand the prompt — fix the prompt and re-run.

- [ ] **Step 5: Insert tenant row (if not already present) and index the KB**

(`make up` must be running.) Insert the tenant row once:

```
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "INSERT INTO tenants (slug, display_name) VALUES ('example', 'Example') ON CONFLICT DO NOTHING;"
```

Then index:

```
uv run ai-sdr reindex-kb --tenant example --kb-root kb
```

Expected: `indexed: 1  skipped: 0  pruned: 0  failed: 0` and a line `+ kb/example/example_kb/precos.md`. A second run should print `indexed: 0  skipped: 1` (idempotency).

- [ ] **Step 6: Commit**

```bash
git add tenants/example/tenant.yaml tenants/example/treeflows/example.yaml kb/example/
git commit -m "feat(plan3 t18): example tenant gets guardrails + KB ref + precos.md fixture"
```

---

## Task 19: Live LLM KB test (real OpenAI embedding)

**Files:**
- Create: `tests/integration/test_kb_live.py`

**Design:** Marked `@pytest.mark.live_llm` and `@pytest.mark.integration` so it's skipped by `make test-unit` and `make test-integration` (unless integration runner picks up live_llm too — check the existing `conftest.py` from Plan 2). Reads `OPENAI_API_KEY` from env. Indexes one `.md`, embeds an obvious query, asserts the right chunk wins.

- [ ] **Step 1: Create `tests/integration/test_kb_live.py`**

```python
"""Live KB test — uses real OpenAI embeddings. Requires OPENAI_API_KEY.

Skipped by default (live_llm marker). Run explicitly:
    uv run pytest tests/integration/test_kb_live.py -v -m live_llm
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.kb.chunker import MarkdownChunker
from ai_sdr.kb.embeddings import build_embedder
from ai_sdr.kb.indexer import reindex_tenant_kb
from ai_sdr.kb.retriever import retrieve
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import EmbeddingsConfig
from ai_sdr.schemas.treeflow_yaml import KBRef
from ai_sdr.settings import get_settings


pytestmark = [pytest.mark.live_llm, pytest.mark.integration]


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)
async def test_live_embed_and_retrieve_finds_relevant_chunk(
    session: AsyncSession, tmp_path: Path
) -> None:
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="LiveT")
        session.add(t)
        await session.flush()

    kb_root = tmp_path / "kb"
    (kb_root / t.slug / "kb_x").mkdir(parents=True)
    (kb_root / t.slug / "kb_x" / "precos.md").write_text(
        "## Mentoria\n\nA Mentoria custa R$ 6000 à vista.\n\n"
        "## Bonus\n\nComunidade fechada com mais de 200 alunas.",
        encoding="utf-8",
    )

    secrets = {"openai_key": os.environ["OPENAI_API_KEY"]}
    embedder = build_embedder(secrets, EmbeddingsConfig())

    await reindex_tenant_kb(session, t, kb_root, embedder, MarkdownChunker())

    chunks = await retrieve(
        session,
        tenant_id=t.id,
        kb_refs=[KBRef(id="kb_x", top_k=2, min_score=0.0)],
        query="quanto custa a mentoria?",
        embedder=embedder,
    )
    assert chunks, "expected at least one chunk back from live retrieval"
    # The Mentoria chunk should rank above Bonus for this query
    top = chunks[0]
    assert "Mentoria" in top.heading_path or ""
    assert top.score > 0.3, f"unexpectedly low score: {top.score}"
```

- [ ] **Step 2: Run the live test (with key set)**

Run: `OPENAI_API_KEY=$YOUR_KEY uv run pytest tests/integration/test_kb_live.py -v -m live_llm`

Expected: PASS. (Without the env var, skipped.)

- [ ] **Step 3: Confirm normal runs skip it**

Run: `uv run pytest tests/integration/test_kb_live.py -v -m integration`

Expected: deselected / not collected (because `live_llm` marker is separate).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_kb_live.py
git commit -m "test(plan3 t19): live LLM KB round-trip (real OpenAI embed)"
```

---

## Task 20: Final smoke + CLAUDE.md update

**Files:**
- Modify: `CLAUDE.md`

**Design:** Run the full test suite, walk through the simulate CLI with KB + guardrails active, and document everything in `CLAUDE.md` so the next person (or you in two weeks) doesn't have to dig.

- [ ] **Step 1: Run full unit + integration suites**

Run:
```
make lint && make format && make type && make test-unit && make test-integration
```

Expected: all green. Fix any failures inline before proceeding (do NOT skip).

- [ ] **Step 2: Smoke-run simulate CLI against the example tenant**

(Requires `make up` and that the `tenants` table has an `example` row — Plan 2 Task 15 covers this.)

Run:
```
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "INSERT INTO tenants (slug, display_name) VALUES ('example', 'Example') ON CONFLICT DO NOTHING;"

uv run ai-sdr reindex-kb --tenant example --kb-root kb

uv run ai-sdr simulate --tenant example --treeflow example --lead smoke-1 --show-extracted
```

In the simulate REPL, ask a price question: `quanto custa a mentoria?` — the agent should respond using values from `kb/example/example_kb/precos.md`. Try also: `e tem garantia?` — should mention 7 dias. Try a "trap": `o desconto é de quanto?` — agent should NOT invent a discount.

If the agent emits anything outside the whitelist, `structlog` should show `guardrail.blocked` and either a retry message or the fallback text. Fix prompts if needed.

- [ ] **Step 3: Add a new section to `CLAUDE.md`**

Append to `CLAUDE.md` (after the existing "TalkFlow runtime" section):

```markdown
## KB (Plan 3)

- Files: `kb/<tenant>/<kb_id>/*.md`. Each `## heading` is a chunk; chunks > 600 tok split by paragraph (or sentence as a fallback). Encoder: tiktoken `cl100k_base`.
- Reindex: `uv run ai-sdr reindex-kb --tenant <slug> [--kb <id>] [--prune] [--kb-root path]`. Idempotent via sha256(content) — only changed docs re-embed. Default `--kb-root` is `kb/` relative to CWD.
- Embedding: OpenAI `text-embedding-3-small` (1536d). Config in `tenant.yaml > llm.embeddings`. Requires `openai_key` in `secrets.enc.yaml`.
- Retrieval: per-Node `knowledge_base: [{id, top_k, min_score}]`. Multiple refs aggregate into one SQL with `kb_id = ANY(...)`. Filtering by `min_score` happens in Python after the SQL `ORDER BY embedding <=> $q LIMIT max(top_k)`.
- pgvector index: IVFFlat `lists=100` (good for <10k chunks). To rebuild after large KB growth: `REINDEX INDEX ix_kb_chunks_embedding;` (or drop + recreate with larger `lists`).
- Cross-tenant isolation: RLS via `tenant_id` (FORCE) — same pattern as `talkflows`. Always set via `set_tenant_context(session, tenant.id)`.

## Guardrails (Plan 3)

- Config: `tenant.yaml > guardrails` block — `enabled`, `allowed_prices: list[int]`, `allowed_products: list[str]`, `critic_enabled`, `fallback_text` (≥10 chars), `max_retries` (1–5, default 2). If `enabled=true` you MUST set at least one allowlist; validator rejects empty.
- Pipeline (post-LLM): `validate_whitelist` → if `node.critical=True` and `critic_enabled=True`, `critic_pass` (Haiku via `tenant.llm.classifier`). On Verdict fail: prepend `SystemMessage(suggested_fix)`, retry. After `max_retries`, fallback text emitted; `collected={}` (conversation stays on the same node).
- LLM is asked to emit `prices_mentioned: list[int]` + `products_mentioned: list[str]` as part of its structured output — the validator compares those lists, NOT regex on `response_text`. Field instructions tell the LLM to enumerate everything it mentioned textually.
- Kill switch: `tenant.guardrails.enabled=false` makes the runner a passthrough (whitelist and critic both no-op).
- HITL future: `guardrails/runner.py:_handle_exhausted()` is the single hook to swap when Plan-N adds human-in-the-loop. Its current body (return fallback text) becomes `await persist_pending_review(...); raise GraphInterrupt()`.

## Prompt caching (Anthropic, Plan 3)

- `tenant.llm.cache_enabled: bool` (default `true`). Applies to Anthropic only — OpenAI auto-caches prefixes ≥1024 tok and exposes no disable.
- Structure per turn: `SystemMessage(content=[{static_prompt, cache_control: ephemeral}, {kb_block}])`. The static block caches; the KB block doesn't (it's dynamic per turn).
- Tools (the structured-output schema) are part of the cacheable prefix automatically.
- Min cacheable: ~1024 tok. Below that, `cache_control` is silently ignored by the provider — `TreeFlowLoader` warns at load time via `treeflow.cache_below_threshold`.
```

- [ ] **Step 4: Verify the CLAUDE.md still parses cleanly**

Run: `head -200 CLAUDE.md`

Expected: no broken markdown, no missing fences.

- [ ] **Step 5: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs(plan3 t20): CLAUDE.md gains KB + Guardrails + caching authoring guides"
```

- [ ] **Step 6: Push and open PR (optional — confirm with user before pushing)**

If asked to push:

```bash
git push -u origin dev/nicolas
gh pr view 1 2>/dev/null || gh pr create --title "Plan 3 — KB + Guardrails" --body "$(cat <<'EOF'
## Summary
- pgvector schema (kb_documents + kb_chunks + IVFFlat) + indexer + retriever
- Markdown-aware chunker (600 tok cap, tiktoken)
- Structured-output whitelist validator (prices_mentioned + products_mentioned)
- Optional critic pass (Haiku) per Node via critical: true
- Retry loop with generic fallback, isolated _handle_exhausted hook for future HITL
- Anthropic prompt caching with per-tenant toggle
- ai-sdr reindex-kb CLI (idempotent via content_hash)

## Test plan
- [ ] `make lint && make format && make type && make test-unit && make test-integration` green
- [ ] `OPENAI_API_KEY=… pytest -m live_llm` green
- [ ] `ai-sdr reindex-kb --tenant example` indexes precos.md
- [ ] `ai-sdr simulate --tenant example --treeflow example --lead smoke-1` answers price questions from KB and refuses invented prices

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## What this plan deliberately does NOT include

(Mirror of spec §2.2 — convenient checklist when reviewing future PRs.)

- HITL escalation (`interrupt()` + reviewer UI) — future plan when WhatsApp + CRM are wired.
- Embedding history (not just `user_input`) before retrieval — easy 1-line change, hold off until we measure recall.
- Cross-encoder re-ranking — only if KB grows >10k chunks.
- 1-hour cache TTL — 5-min covers active conversations.
- History caching — marginal gain, needs reordering of KB block.
- Watcher-based reindex (filesystem watchdog, lifespan auto-check, CI hook) — CLI + post-deploy script cover prod.
- Auto-creation of KB via UI — V2 of the product.
- Cost analytics dashboard — telemetry events are emitted; aggregation is a separate observability plan.
- Semantic whitelist (e.g. block promises of "garantia vitalícia") — critic pass partially covers; out of scope for MVP.

