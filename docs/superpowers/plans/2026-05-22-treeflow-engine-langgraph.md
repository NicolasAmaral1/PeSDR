# TreeFlow Engine + LangGraph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the conversation engine. After this plan, you can declare a TreeFlow in YAML, instantiate a TalkFlow for a tenant+lead, feed text messages via a CLI, and watch the agent traverse Nodes, extract structured fields with real LLM calls (Anthropic or OpenAI), and persist state in Postgres — all without WhatsApp or CRM integration.

**Architecture:** TreeFlow YAML → Pydantic schema → LangGraph `StateGraph`. Each Node compiles into a graph node that calls an LLM with `with_structured_output` to produce the user-facing `response_text` + the fields declared in `collects`. After each node, a router evaluates the Node's `exit_condition` against the accumulated `state.collected`; if satisfied, it evaluates `next_nodes` (using `simpleeval` for safe rule expressions) to pick the next Node; otherwise it stays on the current Node and ends the turn. State is persisted per-thread via `langgraph-checkpoint-postgres` (psycopg3); each TalkFlow row in Postgres (RLS-scoped by `tenant_id`) is the source of truth for "which TreeFlow version is this lead on right now," while the checkpointer tables hold the LangGraph state snapshots keyed by a tenant-prefixed `thread_id`. The Engine has zero knowledge of WhatsApp/CRM — its inputs are plain strings and its outputs are plain strings; an interactive `ai-sdr simulate` CLI exercises it end-to-end against a real LLM.

**Tech Stack additions:** `langgraph` · `langgraph-checkpoint-postgres` · `psycopg[binary,pool]` (3.x, required by the postgres checkpointer) · `langchain-core` · `langchain-anthropic` · `langchain-openai` · `simpleeval` (safe expression evaluator for transitions) · `typer` (CLI). Tests use LangChain's `FakeListChatModel` for deterministic unit/integration runs; one optional `@pytest.mark.live_llm` test exercises real Anthropic + OpenAI calls when API keys are set.

---

## File Structure

```
src/ai_sdr/
├── schemas/
│   ├── llm_yaml.py                       # LLMConfig, LLMDefaults (used in tenant.yaml and TreeFlow)
│   ├── tenant_yaml.py                    # MODIFIED: adds llm: LLMDefaults
│   └── treeflow_yaml.py                  # NEW: TreeFlow, NodeSpec, CollectField, ExitCondition, Transition, FollowUpConfig, GlobalObjection
├── treeflow/
│   ├── __init__.py                       # NEW (empty)
│   ├── loader.py                         # NEW: TreeFlowLoader (YAML → TreeFlow, cache)
│   ├── expressions.py                    # NEW: safe rule evaluator (simpleeval wrapper)
│   ├── state.py                          # NEW: TalkFlowState TypedDict + reducers
│   ├── compiler.py                       # NEW: compile_treeflow(tf, llm_factory, secrets) → CompiledStateGraph
│   ├── checkpointer.py                   # NEW: build AsyncPostgresSaver from settings (sqlalchemy URL → psycopg DSN)
│   └── runtime.py                        # NEW: TalkFlowRuntime — publish_version / create / step / get_history
├── llm/
│   ├── __init__.py                       # NEW (empty)
│   ├── factory.py                        # NEW: build_llm(cfg, secrets) → BaseChatModel (anthropic | openai)
│   └── extractor.py                      # NEW: build_structured_model(collects, response_field) → Pydantic model
├── models/
│   ├── __init__.py                       # MODIFIED: re-export TreeflowVersion + TalkFlow
│   ├── treeflow_version.py               # NEW: treeflow_versions table
│   └── talkflow.py                       # NEW: talkflows table
├── cli/
│   ├── __init__.py                       # NEW (empty)
│   ├── app.py                            # NEW: typer app root (`ai-sdr` command)
│   └── simulate.py                       # NEW: `ai-sdr simulate --tenant X --treeflow Y --lead Z`
└── main.py                               # MODIFIED: lifespan calls checkpointer.setup() once

migrations/versions/
├── 0003_treeflow_tables.py               # NEW: treeflow_versions + talkflows + RLS
└── 0004_checkpointer_setup.py            # NEW: invokes AsyncPostgresSaver.setup() via raw SQL fallback (idempotent)

tenants/example/
├── tenant.yaml                           # MODIFIED: add llm block + treeflows list
├── secrets.enc.yaml                      # MODIFIED: re-encrypt with anthropic_key + openai_key
└── treeflows/
    └── example.yaml                      # NEW: 3-node demo TreeFlow used by tests + CLI smoke

tests/
├── unit/
│   ├── test_treeflow_yaml_schema.py      # NEW
│   ├── test_treeflow_loader.py           # NEW (uses tmp_path; pure-filesystem unit)
│   ├── test_expressions.py               # NEW
│   ├── test_llm_factory.py               # NEW (mocks ChatAnthropic/ChatOpenAI imports)
│   ├── test_extractor.py                 # NEW (FakeListChatModel)
│   └── test_treeflow_compiler.py         # NEW (FakeListChatModel — exercises a 3-node graph in-memory)
└── integration/
    ├── test_treeflow_models.py           # NEW (DB models + RLS)
    ├── test_checkpointer_postgres.py     # NEW (AsyncPostgresSaver round-trip)
    ├── test_talkflow_runtime.py          # NEW (FakeListChatModel; end-to-end through Postgres)
    ├── test_talkflow_runtime_live.py     # NEW (marked live_llm; real Anthropic + OpenAI)
    └── test_simulate_cli.py              # NEW (FakeListChatModel via env override)

pyproject.toml                            # MODIFIED: deps + [project.scripts] ai-sdr=ai_sdr.cli.app:app + new markers
CLAUDE.md                                 # MODIFIED: TreeFlow authoring + simulate CLI + checkpointer notes
.env.example                              # MODIFIED: ANTHROPIC_API_KEY, OPENAI_API_KEY (optional)
```

**Layout notes:**
- `treeflow/` is the new heart of the engine. `llm/` is a thin provider-abstraction layer (factory + extractor) — kept separate because later plans (guardrails, classifier) will reuse it without depending on TreeFlow internals.
- The CLI lives under `src/ai_sdr/cli/` so it ships as part of the wheel and can be invoked both as `python -m ai_sdr` (after `__main__.py` is added) **and** as `ai-sdr` via `[project.scripts]`. This plan only adds the second form; `__main__.py` can wait.
- No `langgraph-checkpoint-postgres` tables in alembic. The lib owns its own DDL (`checkpoints`, `checkpoint_writes`, `checkpoint_migrations`); we invoke `await AsyncPostgresSaver.setup()` once at app/CLI startup. We add a no-op alembic stamp (`0004_checkpointer_setup.py`) only so the migration history records "checkpointer expected to exist" — it does not create the tables (since they require psycopg3, not the asyncpg/SQLAlchemy alembic env).

---

## Prerequisites (delta from Plan 1)

Plan 1's prereqs (Docker, uv, age, sops) still apply. Add:

- **An Anthropic API key** — required for the `live_llm` integration test and the simulate CLI demo. Get one at https://console.anthropic.com/.
- **An OpenAI API key** (optional but recommended for cross-provider testing) — https://platform.openai.com/api-keys.

These keys are loaded **only from the tenant's encrypted `secrets.enc.yaml`** in production code paths. The `.env.example` change in Task 1 is purely for the `live_llm` test fixture (which reads `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` directly to avoid needing sops in CI). All non-live-LLM tests use `FakeListChatModel` and do **not** need any API key.

### VPS notes

Same VPS (`vps-nova`), same ports (Postgres `15432`, Redis `16379`, API `8200`). The checkpointer connects to the same Postgres instance via psycopg3; no new infra.

---

## Task 1: Add dependencies and CLI entrypoint

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: Edit `pyproject.toml` — add runtime deps**

In `[project].dependencies`, append:

```toml
    "langgraph>=0.2.60",
    "langgraph-checkpoint-postgres>=2.0.21",
    "psycopg[binary,pool]>=3.2.3",
    "langchain-core>=0.3.28",
    "langchain-anthropic>=0.3.0",
    "langchain-openai>=0.2.14",
    "simpleeval>=1.0.3",
    "typer>=0.15",
```

So the final block reads (showing only the deps list):

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
    "typer>=0.15",
]
```

- [ ] **Step 2: Edit `pyproject.toml` — register the `ai-sdr` CLI entrypoint**

Add a new top-level section (anywhere before `[build-system]`):

```toml
[project.scripts]
ai-sdr = "ai_sdr.cli.app:app"
```

- [ ] **Step 3: Edit `pyproject.toml` — add new pytest markers and a mypy override**

Replace the `[tool.pytest.ini_options].markers` list with:

```toml
markers = [
    "integration: tests that require docker (postgres, redis)",
    "live_llm: tests that hit real LLM APIs (requires ANTHROPIC_API_KEY and/or OPENAI_API_KEY)",
]
```

Append a new mypy override block (after the existing ones):

```toml
[[tool.mypy.overrides]]
module = "simpleeval.*"
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = "langgraph.*"
ignore_missing_imports = true
```

- [ ] **Step 4: Edit `.env.example` — add API keys**

Append:

```
# LLM API keys — only required for live_llm tests and the simulate CLI demo
# In production these are read from each tenant's secrets.enc.yaml, NOT from .env
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

- [ ] **Step 5: Sync deps and verify imports**

Run:

```bash
uv sync
uv run python -c "import langgraph, langchain_anthropic, langchain_openai, simpleeval, typer, psycopg; print('ok')"
```

Expected: `ok`.

- [ ] **Step 6: Lint + types still clean**

Run:

```bash
make lint
make type
```

Expected: no errors. (`type` may warn that `ai_sdr.cli.app` doesn't exist yet — that's OK, mypy doesn't run on `[project.scripts]` strings. If it complains anywhere, leave it; Task 14 creates the file.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .env.example
git commit -m "chore(plan2): add langgraph, langchain, psycopg, simpleeval, typer deps"
```

---

## Task 2: LLMConfig schema + extend TenantConfig

**Files:**
- Create: `src/ai_sdr/schemas/llm_yaml.py`
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Modify: `tests/unit/test_tenant_yaml_schema.py` (add cases)

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_tenant_yaml_schema.py`:

```python
def test_tenant_yaml_accepts_llm_block() -> None:
    data = {
        "id": "x",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "temperature": 0.7,
                "api_key_ref": "secrets/anthropic_key",
            },
            "classifier": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "api_key_ref": "secrets/anthropic_key",
            },
        },
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.llm is not None
    assert cfg.llm.default.provider == "anthropic"
    assert cfg.llm.default.model == "claude-sonnet-4-6"
    assert cfg.llm.classifier is not None
    assert cfg.llm.classifier.model == "claude-haiku-4-5"


def test_llm_provider_must_be_known() -> None:
    data = {
        "id": "x",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "bogus_provider",
                "model": "x",
                "api_key_ref": "secrets/x",
            }
        },
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)


def test_llm_api_key_ref_must_start_with_secrets_slash() -> None:
    data = {
        "id": "x",
        "display_name": "X",
        "timezone": "America/Sao_Paulo",
        "llm": {
            "default": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_ref": "sk-ant-PLAINTEXT-LEAK",
            }
        },
    }
    with pytest.raises(ValidationError):
        TenantConfig.model_validate(data)
```

- [ ] **Step 2: Run tests (expect fail — `cfg.llm` is missing)**

Run: `uv run pytest tests/unit/test_tenant_yaml_schema.py -v`

Expected: the three new tests FAIL with AttributeError or ValidationError mismatch.

- [ ] **Step 3: Create `src/ai_sdr/schemas/llm_yaml.py`**

```python
"""Pydantic schemas for LLM configuration (used by tenant.yaml and TreeFlow node overrides)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProviderName = Literal["anthropic", "openai"]


class LLMConfig(BaseModel):
    """A single LLM call configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0, le=64_000)
    api_key_ref: str

    @field_validator("api_key_ref")
    @classmethod
    def _api_key_ref_is_a_secret_ref(cls, v: str) -> str:
        if not v.startswith("secrets/"):
            raise ValueError(
                "api_key_ref must reference a SOPS secret (e.g. 'secrets/anthropic_key'); "
                "never embed the key directly"
            )
        return v


class LLMDefaults(BaseModel):
    """Tenant-level LLM defaults — Nodes inherit `default` unless they override."""

    model_config = ConfigDict(extra="forbid")

    default: LLMConfig
    classifier: LLMConfig | None = None
```

- [ ] **Step 4: Edit `src/ai_sdr/schemas/tenant_yaml.py`** — add the import and field.

Add this import near the top:

```python
from ai_sdr.schemas.llm_yaml import LLMDefaults
```

Add this field on `TenantConfig` (after `conversation`):

```python
    llm: LLMDefaults | None = None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_tenant_yaml_schema.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/schemas/llm_yaml.py src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml_schema.py
git commit -m "feat(plan2 t2): LLMConfig schema + tenant.yaml llm block"
```

---

## Task 3: TreeFlow YAML schemas

**Files:**
- Create: `src/ai_sdr/schemas/treeflow_yaml.py`
- Create: `tests/unit/test_treeflow_yaml_schema.py`

**Scope:** This task implements the Pydantic schemas only — no I/O, no compilation. Subset of spec §5.0 + §5.2 needed for the engine MVP. Fields deferred to later plans: `knowledge_base` (Plan 3, KB), `handles_objections` (Plan 4, classifier), `sync_to_crm` (Plan 5, CRM), `critical` (Plan 3, guardrails). They are accepted by the schema (forward-compat) as opaque `dict | None` so we don't break later — but we document that nothing uses them yet.

- [ ] **Step 1: Write the failing schema tests**

`tests/unit/test_treeflow_yaml_schema.py`:

```python
import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import (
    CollectField,
    ExitCondition,
    NodeSpec,
    TreeFlow,
)


# ---------- minimal happy paths ----------

def test_minimal_treeflow_validates() -> None:
    data = {
        "id": "mentoria",
        "version": "1.0.0",
        "display_name": "Funil Mentoria",
        "entry_node": "saudacao",
        "nodes": [
            {
                "id": "saudacao",
                "prompt": "Diga olá em PT-BR.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    assert tf.id == "mentoria"
    assert tf.version == "1.0.0"
    assert tf.entry_node == "saudacao"
    assert tf.nodes[0].id == "saudacao"
    assert tf.nodes[0].exit_condition.type == "all_fields_filled"


def test_node_collects_and_transitions() -> None:
    data = {
        "id": "tf",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "q",
        "nodes": [
            {
                "id": "q",
                "prompt": "Pergunte X.",
                "collects": [
                    {
                        "field": "faturamento_mensal",
                        "type": "number",
                        "extraction_hint": "valor mensal em R$",
                        "required": True,
                        "validation": {"min": 0},
                    },
                    {"field": "tempo_mercado", "type": "text", "required": True},
                ],
                "exit_condition": {
                    "type": "rule_expression",
                    "expression": "faturamento_mensal != None and tempo_mercado != None",
                },
                "next_nodes": [
                    {"condition": "faturamento_mensal >= 30000", "target": "oferta_premium"},
                    {"condition": "faturamento_mensal < 30000", "target": "oferta_basica"},
                ],
            },
            {
                "id": "oferta_premium",
                "prompt": "Apresente premium.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
            {
                "id": "oferta_basica",
                "prompt": "Apresente básica.",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
        ],
    }
    tf = TreeFlow.model_validate(data)
    q = tf.nodes[0]
    assert len(q.collects) == 2
    assert q.collects[0].field == "faturamento_mensal"
    assert q.collects[0].validation == {"min": 0}
    assert len(q.next_nodes) == 2


def test_treeflow_with_followup_and_global_objections() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "follow_up": {
            "enabled": True,
            "max_attempts": 3,
            "sequence": [
                {"after": "24h", "template": "Oi {{nome}}!"},
                {"after": "72h", "template": "Tá aí?"},
            ],
        },
        "global_objections": [
            {"id": "preciso_pensar", "kb": "kb_obj_pensar"},
            {"id": "falta_tempo", "kb": "kb_obj_tempo"},
        ],
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    tf = TreeFlow.model_validate(data)
    assert tf.follow_up is not None
    assert tf.follow_up.max_attempts == 3
    assert tf.follow_up.sequence[0].after == "24h"
    assert len(tf.global_objections) == 2


# ---------- structural validations ----------

def test_entry_node_must_exist_in_nodes() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "ghost",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="entry_node"):
        TreeFlow.model_validate(data)


def test_transition_target_must_exist_or_be_END() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "missing_node"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="missing_node"):
        TreeFlow.model_validate(data)


def test_duplicate_node_ids_rejected() -> None:
    data = {
        "id": "x",
        "version": "0.1.0",
        "display_name": "X",
        "entry_node": "a",
        "nodes": [
            {
                "id": "a",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
            {
                "id": "a",
                "prompt": "p2",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            },
        ],
    }
    with pytest.raises(ValidationError, match="duplicate"):
        TreeFlow.model_validate(data)


def test_rule_expression_exit_requires_expression_field() -> None:
    with pytest.raises(ValidationError, match="expression"):
        ExitCondition.model_validate({"type": "rule_expression"})


def test_collect_field_type_must_be_known() -> None:
    with pytest.raises(ValidationError):
        CollectField.model_validate({"field": "x", "type": "telepathy"})


def test_node_id_must_be_slug() -> None:
    with pytest.raises(ValidationError):
        NodeSpec.model_validate(
            {
                "id": "Bad ID",
                "prompt": "p",
                "exit_condition": {"type": "all_fields_filled"},
                "next_nodes": [{"condition": "true", "target": "END"}],
            }
        )
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_treeflow_yaml_schema.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.schemas.treeflow_yaml'`.

- [ ] **Step 3: Create `src/ai_sdr/schemas/treeflow_yaml.py`**

```python
"""Pydantic schemas for TreeFlow YAML files.

A TreeFlow is the static definition of a conversation funnel. It is compiled
into a LangGraph StateGraph at runtime (see ai_sdr.treeflow.compiler).

Fields scoped out of plan 2 are accepted as forward-compatible opaque blobs:
- knowledge_base (Plan 3 — KB)
- handles_objections (Plan 4 — classifier)
- sync_to_crm (Plan 5 — CRM)
- critical (Plan 3 — guardrails)
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai_sdr.schemas.llm_yaml import LLMConfig

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}[a-z0-9]$")
END_SENTINEL = "END"

CollectType = Literal["text", "number", "boolean", "email", "phone"]
ExitConditionType = Literal["all_fields_filled", "rule_expression", "combined"]


class CollectField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    type: CollectType
    extraction_hint: str | None = None
    required: bool = False
    validation: dict[str, Any] | None = None


class ExitCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ExitConditionType
    expression: str | None = None
    fallback: Literal["llm_judge"] | None = None

    @model_validator(mode="after")
    def _expression_required_for_rule(self) -> "ExitCondition":
        if self.type in {"rule_expression", "combined"} and not self.expression:
            raise ValueError(
                f"exit_condition.expression is required when type={self.type!r}"
            )
        return self


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    target: str = Field(min_length=1)


class FollowUpStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after: str = Field(pattern=r"^\d+(s|m|h|d)$")  # e.g. "24h", "30m", "7d"
    template: str = Field(min_length=1)


class FollowUpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    sequence: list[FollowUpStep] = Field(default_factory=list)


class GlobalObjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)


class NodeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str = Field(min_length=1)
    llm: LLMConfig | None = None
    collects: list[CollectField] = Field(default_factory=list)
    exit_condition: ExitCondition
    next_nodes: list[Transition] = Field(min_length=1)

    # forward-compat — accepted but unused in plan 2
    knowledge_base: list[dict[str, Any]] | None = None
    handles_objections: list[dict[str, Any]] | None = None
    sync_to_crm: str | None = None
    critical: bool = False

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, v: str) -> str:
        if not NODE_ID_RE.match(v):
            raise ValueError(
                "node id must be a slug: lowercase, digits, underscores; "
                "start with a letter; 2-64 chars; end with letter or digit"
            )
        return v


class TreeFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    display_name: str = Field(min_length=1)
    follow_up: FollowUpConfig | None = None
    global_objections: list[GlobalObjection] = Field(default_factory=list)
    entry_node: str
    nodes: list[NodeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_graph_consistency(self) -> "TreeFlow":
        ids = [n.id for n in self.nodes]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"duplicate node ids: {sorted(dupes)}")

        valid_targets = set(ids) | {END_SENTINEL}
        if self.entry_node not in ids:
            raise ValueError(
                f"entry_node={self.entry_node!r} is not declared in nodes "
                f"(declared: {ids})"
            )
        for node in self.nodes:
            for tr in node.next_nodes:
                if tr.target not in valid_targets:
                    raise ValueError(
                        f"node {node.id!r} transitions to unknown target {tr.target!r} "
                        f"(must be one of: {sorted(valid_targets)})"
                    )
        return self
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_treeflow_yaml_schema.py -v`

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/treeflow_yaml.py tests/unit/test_treeflow_yaml_schema.py
git commit -m "feat(plan2 t3): TreeFlow YAML pydantic schemas + graph consistency validations"
```

---

## Task 4: TreeFlow loader

**Files:**
- Create: `src/ai_sdr/treeflow/__init__.py` (empty)
- Create: `src/ai_sdr/treeflow/loader.py`
- Create: `tests/unit/test_treeflow_loader.py`

- [ ] **Step 1: Create empty `src/ai_sdr/treeflow/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_treeflow_loader.py`:

```python
from pathlib import Path

import pytest

from ai_sdr.treeflow.loader import (
    TreeFlowLoader,
    TreeFlowNotFoundError,
)

MIN_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: a
nodes:
  - id: a
    prompt: Hi.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
"""


@pytest.fixture
def loader(tmp_path: Path) -> TreeFlowLoader:
    (tmp_path / "tenants" / "t1" / "treeflows").mkdir(parents=True)
    (tmp_path / "tenants" / "t1" / "treeflows" / "demo.yaml").write_text(MIN_YAML)
    return TreeFlowLoader(tenants_dir=tmp_path / "tenants")


def test_load_valid_treeflow(loader: TreeFlowLoader) -> None:
    tf = loader.load("t1", "demo")
    assert tf.id == "demo"
    assert tf.version == "0.1.0"
    assert tf.entry_node == "a"


def test_load_caches_result(loader: TreeFlowLoader) -> None:
    a = loader.load("t1", "demo")
    b = loader.load("t1", "demo")
    assert a is b


def test_load_missing_treeflow_raises(loader: TreeFlowLoader) -> None:
    with pytest.raises(TreeFlowNotFoundError):
        loader.load("t1", "ghost")


def test_reload_bypasses_cache(loader: TreeFlowLoader) -> None:
    a = loader.load("t1", "demo")
    b = loader.reload("t1", "demo")
    assert a is not b
    assert a == b
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_treeflow_loader.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Create `src/ai_sdr/treeflow/loader.py`**

```python
"""Load + validate + cache TreeFlow YAML files from `tenants/<id>/treeflows/`."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_sdr.schemas.treeflow_yaml import TreeFlow


class TreeFlowNotFoundError(Exception):
    """Raised when a TreeFlow YAML file does not exist."""


class TreeFlowLoader:
    """Read TreeFlow YAML files per tenant. Cache by (tenant_id, treeflow_id)."""

    def __init__(self, tenants_dir: Path) -> None:
        self._tenants_dir = Path(tenants_dir)
        self._cache: dict[tuple[str, str], TreeFlow] = {}

    def load(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        key = (tenant_id, treeflow_id)
        if key in self._cache:
            return self._cache[key]
        tf = self._read(tenant_id, treeflow_id)
        self._cache[key] = tf
        return tf

    def reload(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        tf = self._read(tenant_id, treeflow_id)
        self._cache[(tenant_id, treeflow_id)] = tf
        return tf

    def raw_yaml(self, tenant_id: str, treeflow_id: str) -> str:
        """Return the raw YAML text (used by runtime to snapshot a version)."""
        path = self._path(tenant_id, treeflow_id)
        if not path.is_file():
            raise TreeFlowNotFoundError(f"treeflow not found at {path}")
        return path.read_text(encoding="utf-8")

    def _path(self, tenant_id: str, treeflow_id: str) -> Path:
        return self._tenants_dir / tenant_id / "treeflows" / f"{treeflow_id}.yaml"

    def _read(self, tenant_id: str, treeflow_id: str) -> TreeFlow:
        path = self._path(tenant_id, treeflow_id)
        if not path.is_file():
            raise TreeFlowNotFoundError(f"treeflow not found at {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return TreeFlow.model_validate(data)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_treeflow_loader.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/__init__.py src/ai_sdr/treeflow/loader.py tests/unit/test_treeflow_loader.py
git commit -m "feat(plan2 t4): TreeFlow loader with cache + raw_yaml accessor"
```

---

## Task 5: Safe expression evaluator (transitions DSL)

**Files:**
- Create: `src/ai_sdr/treeflow/expressions.py`
- Create: `tests/unit/test_expressions.py`

**Rationale:** transitions in YAML look like `"faturamento_mensal >= 30000"` or `"lead_disse_nao"`. Evaluating user-controlled strings is dangerous — `eval()` is out of the question. `simpleeval` parses the expression into Python's AST and walks only a whitelisted set of node types (comparisons, boolean ops, simple names, literals, `in`). Anything else raises. We expose two helpers: `eval_bool(expr, ctx)` for transitions/`rule_expression` exit conditions, and `is_set(name)` as the only built-in function (true if `name` is present and not None in ctx).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_expressions.py`:

```python
import pytest

from ai_sdr.treeflow.expressions import (
    ExpressionError,
    eval_bool,
)


def test_simple_true_literal() -> None:
    assert eval_bool("true", {}) is True


def test_comparison_against_number() -> None:
    assert eval_bool("faturamento >= 30000", {"faturamento": 50000}) is True
    assert eval_bool("faturamento >= 30000", {"faturamento": 1000}) is False


def test_boolean_operators() -> None:
    ctx = {"a": True, "b": False, "n": 5}
    assert eval_bool("a and n > 0", ctx) is True
    assert eval_bool("a and b", ctx) is False
    assert eval_bool("not b", ctx) is True


def test_in_operator() -> None:
    assert eval_bool("'sim' in resposta", {"resposta": "sim, claro"}) is True


def test_is_set_helper() -> None:
    assert eval_bool("is_set('email')", {"email": "x@y.com"}) is True
    assert eval_bool("is_set('email')", {"email": None}) is False
    assert eval_bool("is_set('email')", {}) is False


def test_missing_name_treated_as_none() -> None:
    # ergonomics: a transition referencing a not-yet-collected field should be False, not crash
    assert eval_bool("faturamento >= 30000", {}) is False


def test_attribute_access_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("(1).__class__", {})


def test_function_call_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("len([1,2,3]) > 0", {})  # len is not whitelisted


def test_dunder_name_blocked() -> None:
    with pytest.raises(ExpressionError):
        eval_bool("__import__", {})


def test_non_boolean_result_coerced() -> None:
    # truthy values coerce to True
    assert eval_bool("1", {}) is True
    assert eval_bool("0", {}) is False
    assert eval_bool("''", {}) is False
```

- [ ] **Step 2: Run (expect fail — module missing)**

Run: `uv run pytest tests/unit/test_expressions.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/ai_sdr/treeflow/expressions.py`**

```python
"""Safe expression evaluator for TreeFlow transitions and rule_expression exits.

Backed by `simpleeval`, restricted to a small whitelist of AST nodes.
"""

from __future__ import annotations

from typing import Any

from simpleeval import (
    AttributeDoesNotExist,
    FeatureNotAvailable,
    InvalidExpression,
    NameNotDefined,
    SimpleEval,
)


class ExpressionError(Exception):
    """Raised when an expression is malformed or uses forbidden features."""


def _is_set_factory(ctx: dict[str, Any]):
    def is_set(name: str) -> bool:
        return name in ctx and ctx[name] is not None

    return is_set


def eval_bool(expression: str, context: dict[str, Any]) -> bool:
    """Evaluate `expression` against `context` and coerce to bool.

    Missing names resolve to `None` (so transitions on not-yet-collected fields
    evaluate to False instead of raising). Forbidden operations raise
    `ExpressionError`. Built-in helpers: `true`, `false`, `is_set(name)`.
    """

    names: dict[str, Any] = {"true": True, "false": False}
    names.update(context)
    evaluator = SimpleEval(
        names=names,
        functions={"is_set": _is_set_factory(context)},
    )
    try:
        result = evaluator.eval(expression)
    except NameNotDefined:
        return False
    except AttributeDoesNotExist as e:
        raise ExpressionError(f"attribute access not allowed: {e}") from e
    except FeatureNotAvailable as e:
        raise ExpressionError(f"forbidden feature in expression {expression!r}: {e}") from e
    except InvalidExpression as e:
        raise ExpressionError(f"invalid expression {expression!r}: {e}") from e
    except (SyntaxError, ValueError) as e:
        raise ExpressionError(f"could not parse expression {expression!r}: {e}") from e

    return bool(result)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_expressions.py -v`

Expected: all PASS.

> If `test_dunder_name_blocked` fails because `simpleeval` raises `NameNotDefined` for `__import__` (treats it as missing name → False), add an explicit guard at the top of `eval_bool`:
>
> ```python
> if "__" in expression:
>     raise ExpressionError(f"dunder names forbidden in expression {expression!r}")
> ```
>
> Then re-run.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/treeflow/expressions.py tests/unit/test_expressions.py
git commit -m "feat(plan2 t5): simpleeval-based safe transition expression evaluator"
```

---

## Task 6: LLM factory (Anthropic + OpenAI)

**Files:**
- Create: `src/ai_sdr/llm/__init__.py` (empty)
- Create: `src/ai_sdr/llm/factory.py`
- Create: `tests/unit/test_llm_factory.py`

- [ ] **Step 1: Create empty `src/ai_sdr/llm/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_llm_factory.py`:

```python
import pytest

from ai_sdr.llm.factory import (
    LLMSecretNotFoundError,
    UnknownProviderError,
    build_llm,
    resolve_api_key,
)
from ai_sdr.schemas.llm_yaml import LLMConfig


def test_resolve_api_key_reads_from_secrets_dict() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
    secrets = {"anthropic_key": "sk-ant-xxx"}
    assert resolve_api_key(cfg, secrets) == "sk-ant-xxx"


def test_resolve_api_key_raises_when_missing() -> None:
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
    with pytest.raises(LLMSecretNotFoundError, match="anthropic_key"):
        resolve_api_key(cfg, {})


def test_build_llm_anthropic_returns_chat_anthropic_instance() -> None:
    from langchain_anthropic import ChatAnthropic

    cfg = LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        temperature=0.5,
        api_key_ref="secrets/anthropic_key",
    )
    llm = build_llm(cfg, secrets={"anthropic_key": "sk-ant-test"})
    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-sonnet-4-6"
    assert llm.temperature == 0.5


def test_build_llm_openai_returns_chat_openai_instance() -> None:
    from langchain_openai import ChatOpenAI

    cfg = LLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.3,
        api_key_ref="secrets/openai_key",
    )
    llm = build_llm(cfg, secrets={"openai_key": "sk-openai-test"})
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gpt-4o-mini"


def test_build_llm_unknown_provider_raises() -> None:
    # Bypass pydantic validation by constructing the dataclass manually-ish
    # (we instead patch the cfg.provider to simulate a future provider that lands in YAML
    # before factory support).
    cfg = LLMConfig(
        provider="anthropic",
        model="x",
        api_key_ref="secrets/anthropic_key",
    )
    object.__setattr__(cfg, "provider", "wat")
    with pytest.raises(UnknownProviderError):
        build_llm(cfg, secrets={"anthropic_key": "x"})
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_llm_factory.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Create `src/ai_sdr/llm/factory.py`**

```python
"""Build a `BaseChatModel` from an `LLMConfig` + tenant secrets."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from ai_sdr.schemas.llm_yaml import LLMConfig


class LLMSecretNotFoundError(KeyError):
    """The secret referenced by `api_key_ref` is not in the secrets dict."""


class UnknownProviderError(ValueError):
    """The provider in LLMConfig is not registered."""


def resolve_api_key(cfg: LLMConfig, secrets: dict[str, str]) -> str:
    """`api_key_ref` is 'secrets/<name>'; return secrets[<name>]."""
    name = cfg.api_key_ref.removeprefix("secrets/")
    if name not in secrets:
        raise LLMSecretNotFoundError(name)
    return secrets[name]


def build_llm(cfg: LLMConfig, secrets: dict[str, str]) -> BaseChatModel:
    """Instantiate a LangChain chat model based on `cfg.provider`."""
    api_key = resolve_api_key(cfg, secrets)
    kwargs: dict[str, object] = {"temperature": cfg.temperature}
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens

    if cfg.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=cfg.model, api_key=api_key, **kwargs)  # type: ignore[arg-type]

    if cfg.provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=cfg.model, api_key=api_key, **kwargs)  # type: ignore[arg-type]

    raise UnknownProviderError(f"unsupported provider: {cfg.provider!r}")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_llm_factory.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/llm/__init__.py src/ai_sdr/llm/factory.py tests/unit/test_llm_factory.py
git commit -m "feat(plan2 t6): LLM factory for anthropic + openai providers"
```

---

## Task 7: Field extractor (structured output)

**Files:**
- Create: `src/ai_sdr/llm/extractor.py`
- Create: `tests/unit/test_extractor.py`

**Design:** Given a Node's `collects: list[CollectField]`, we dynamically build a Pydantic model containing one optional field per `CollectField` plus a mandatory `response_text: str`. We then wrap any `BaseChatModel` with `.with_structured_output(model)` and return a `Runnable` that takes a `list[BaseMessage]` and returns the model instance. Type mapping: `text→str | None`, `number→float | None`, `boolean→bool | None`, `email→str | None`, `phone→str | None`. (Stricter validation — e.g. EmailStr — can be added later; keeping it loose now avoids the LLM responding "I don't know" with a non-email string and crashing.)

- [ ] **Step 1: Write the failing test**

`tests/unit/test_extractor.py`:

```python
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.llm.extractor import build_structured_model, extract
from ai_sdr.schemas.treeflow_yaml import CollectField


def test_build_model_has_response_text_and_fields() -> None:
    collects = [
        CollectField(field="faturamento", type="number", required=True),
        CollectField(field="email", type="email"),
    ]
    Model = build_structured_model(collects)
    fields = set(Model.model_fields.keys())
    assert fields == {"response_text", "faturamento", "email"}

    # all extracted fields are Optional regardless of `required` (the gate is exit_condition, not pydantic)
    instance = Model(response_text="oi")
    assert instance.faturamento is None
    assert instance.email is None
    assert instance.response_text == "oi"


def test_build_model_rejects_field_named_response_text() -> None:
    collects = [CollectField(field="response_text", type="text")]
    with pytest.raises(ValueError, match="reserved"):
        build_structured_model(collects)


async def test_extract_with_fake_llm_returns_typed_object() -> None:
    collects = [
        CollectField(field="faturamento", type="number"),
        CollectField(field="cidade", type="text"),
    ]
    # FakeListChatModel returns the next string in its `responses` list per call.
    # We override `with_structured_output` to be exercisable by returning a dict
    # directly via a custom runnable below.
    from langchain_core.runnables import RunnableLambda

    expected: dict[str, Any] = {
        "response_text": "Legal! Anotei R$ 50000 e Curitiba.",
        "faturamento": 50000,
        "cidade": "Curitiba",
    }

    class StubLLM:
        def with_structured_output(self, model: Any) -> Any:  # noqa: D401
            return RunnableLambda(lambda _msgs: model.model_validate(expected))

    Model = build_structured_model(collects)
    result = await extract(
        StubLLM(),  # type: ignore[arg-type]
        Model,
        messages=[SystemMessage(content="you are a SDR"), HumanMessage(content="50000, curitiba")],
    )
    assert result.response_text.startswith("Legal!")
    assert result.faturamento == 50000
    assert result.cidade == "Curitiba"


async def test_extract_smoke_with_fake_list_chat_model_does_not_crash() -> None:
    """Sanity: FakeListChatModel doesn't natively support with_structured_output well —
    this test just guards that our import paths are wired correctly. Real provider
    coverage happens in the live_llm test."""
    fake = FakeListChatModel(responses=["irrelevant"])
    assert hasattr(fake, "with_structured_output")
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_extractor.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/ai_sdr/llm/extractor.py`**

```python
"""Dynamic Pydantic model + structured-output runner for a Node's `collects` list."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, create_model

from ai_sdr.schemas.treeflow_yaml import CollectField

RESPONSE_FIELD = "response_text"

_PY_TYPE: dict[str, type] = {
    "text": str,
    "number": float,
    "boolean": bool,
    "email": str,
    "phone": str,
}


def build_structured_model(collects: list[CollectField]) -> type[BaseModel]:
    """Create a Pydantic model: { response_text: str, <each collected field as Optional> }."""

    field_defs: dict[str, Any] = {
        RESPONSE_FIELD: (str, Field(description="What the agent says to the lead next.")),
    }

    for c in collects:
        if c.field == RESPONSE_FIELD:
            raise ValueError(f"{RESPONSE_FIELD!r} is a reserved collect-field name")
        py_type = _PY_TYPE[c.type]
        description = c.extraction_hint or f"Extracted {c.type} field {c.field!r}."
        field_defs[c.field] = (py_type | None, Field(default=None, description=description))

    return create_model("NodeOutput", **field_defs)


async def extract(
    llm: BaseChatModel,
    model: type[BaseModel],
    messages: list[BaseMessage],
) -> BaseModel:
    """Bind the model as structured output and invoke against `messages` (async)."""
    runnable = llm.with_structured_output(model)
    return await runnable.ainvoke(messages)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_extractor.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/llm/extractor.py tests/unit/test_extractor.py
git commit -m "feat(plan2 t7): dynamic pydantic extractor (response_text + collects → structured output)"
```

---

## Task 8: TalkFlow state

**Files:**
- Create: `src/ai_sdr/treeflow/state.py`

No tests — pure types, exercised by Tasks 9+.

- [ ] **Step 1: Create `src/ai_sdr/treeflow/state.py`**

```python
"""TalkFlow state — the typed dict LangGraph persists per thread.

LangGraph treats the state as immutable per node: each node returns a partial dict;
LangGraph merges via per-field reducers. Fields without explicit reducers use
"replace" (the new value wins). For lists we want "append," so we annotate.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class Message(TypedDict):
    role: str  # "user" | "assistant" | "system"
    content: str


class TalkFlowState(TypedDict, total=False):
    # identity (set on create, never mutated)
    tenant_id: str
    lead_id: str
    treeflow_id: str
    treeflow_version: str

    # turn-by-turn dynamic fields
    current_node: str
    collected: dict[str, Any]  # accumulated across nodes; merged with dict.update
    messages: Annotated[list[Message], operator.add]
    last_user_input: str
    last_agent_response: str
    completed: bool  # True when graph reached END
```

- [ ] **Step 2: Verify import**

Run:

```bash
uv run python -c "from ai_sdr.treeflow.state import TalkFlowState, Message; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/ai_sdr/treeflow/state.py
git commit -m "feat(plan2 t8): TalkFlowState typed dict with messages reducer"
```

---

## Task 9: TreeFlow → LangGraph compiler

**Files:**
- Create: `src/ai_sdr/treeflow/compiler.py`
- Create: `tests/unit/test_treeflow_compiler.py`

**Design:** Each Node becomes a graph node fn. The fn:
1. Reads `state["last_user_input"]` and `state["messages"]`, builds a `list[BaseMessage]` containing a `SystemMessage` (from Node.prompt) + the running message history + the new `HumanMessage(last_user_input)` (if present).
2. Builds the LLM via factory (`node.llm` else `tenant.llm.default`).
3. Builds the extractor model from `node.collects`.
4. Calls `extract(llm, model, messages)` → gets `response_text` + extracted fields.
5. Returns a state delta: merges extracted fields into `collected`, appends user+assistant messages, updates `last_agent_response`, **does NOT change `current_node` yet** — the router does that.

After each node, a conditional edge routes to either:
- `END` (the graph stops; this turn is done — the lead will reply later)
- back to itself via another node (same-turn transition) — **disallowed in MVP** because it could LLM-loop; we only transition between turns. So conditional edge maps each node to `END` always, BUT before returning we update `state["current_node"]` to the next node so the next turn's `START` routes there.

Topology:
```
START → router_fn (reads state["current_node"]) → <node> → END
```

The router fn at START picks the right entry node per invocation. Each node fn:
- runs LLM
- if exit_condition met → sets `state["current_node"]` to first matching `next_nodes[i].target` (or "END" sentinel → also sets `completed=True`)
- else → keeps `state["current_node"]` as-is

This gives us natural "one node per user turn" semantics without LangGraph's `interrupt()` (which is for human-in-the-loop pause).

- [ ] **Step 1: Write the failing compiler test**

`tests/unit/test_treeflow_compiler.py`:

```python
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from ai_sdr.llm.factory import build_llm  # noqa: F401 — imported to assert path exists
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.treeflow.compiler import compile_treeflow
from ai_sdr.treeflow.state import TalkFlowState

DEMO_YAML = {
    "id": "demo",
    "version": "0.1.0",
    "display_name": "Demo",
    "entry_node": "saudacao",
    "nodes": [
        {
            "id": "saudacao",
            "prompt": "Cumprimente o lead.",
            "exit_condition": {"type": "all_fields_filled"},  # always true when collects is empty
            "next_nodes": [{"condition": "true", "target": "qualificacao"}],
        },
        {
            "id": "qualificacao",
            "prompt": "Pergunte faturamento.",
            "collects": [{"field": "faturamento", "type": "number", "required": True}],
            "exit_condition": {
                "type": "rule_expression",
                "expression": "faturamento != None",
            },
            "next_nodes": [
                {"condition": "faturamento >= 30000", "target": "premium"},
                {"condition": "faturamento < 30000", "target": "basica"},
            ],
        },
        {
            "id": "premium",
            "prompt": "Oferta premium.",
            "exit_condition": {"type": "all_fields_filled"},
            "next_nodes": [{"condition": "true", "target": "END"}],
        },
        {
            "id": "basica",
            "prompt": "Oferta básica.",
            "exit_condition": {"type": "all_fields_filled"},
            "next_nodes": [{"condition": "true", "target": "END"}],
        },
    ],
}

TENANT_LLM = LLMDefaults(
    default=LLMConfig(
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_key_ref="secrets/anthropic_key",
    )
)


def _stub_llm_factory(per_node_responses: dict[str, dict[str, Any]]):
    """Returns a callable that mimics build_llm but ignores the cfg and returns
    a stub LLM whose .with_structured_output(M) returns a runnable that yields
    M(**per_node_responses[current_node])."""

    class _Stub:
        def __init__(self, current_node: str) -> None:
            self._node = current_node

        def with_structured_output(self, model: type[BaseModel]) -> Any:
            payload = per_node_responses[self._node]
            return RunnableLambda(lambda _msgs: model.model_validate(payload))

    def factory(cfg: LLMConfig, secrets: dict[str, str], current_node: str) -> Any:
        return _Stub(current_node)

    return factory


async def test_compiled_graph_runs_one_node_per_turn_and_routes() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node: dict[str, dict[str, Any]] = {
        "saudacao": {"response_text": "Oi! Tudo bem?"},
        "qualificacao": {"response_text": "Anotado: 50k.", "faturamento": 50000},
        "premium": {"response_text": "Te apresento a Mentoria."},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )

    # turn 1: lead arrives, no input yet — engine sends greeting
    state: TalkFlowState = {
        "tenant_id": "t",
        "lead_id": "l",
        "treeflow_id": "demo",
        "treeflow_version": "0.1.0",
        "current_node": "saudacao",
        "collected": {},
        "messages": [],
        "last_user_input": "",
        "last_agent_response": "",
        "completed": False,
    }
    out1 = await graph.ainvoke(state)
    assert out1["last_agent_response"] == "Oi! Tudo bem?"
    assert out1["current_node"] == "qualificacao"  # saudacao has no collects → exit ok → advances
    assert out1["completed"] is False

    # turn 2: lead replies "faturo 50k"
    out1["last_user_input"] = "faturo 50k"
    out2 = await graph.ainvoke(out1)
    assert out2["last_agent_response"] == "Anotado: 50k."
    assert out2["collected"]["faturamento"] == 50000
    assert out2["current_node"] == "premium"  # 50000 >= 30000 → premium

    # turn 3: lead waits for the offer
    out2["last_user_input"] = ""
    out3 = await graph.ainvoke(out2)
    assert out3["last_agent_response"] == "Te apresento a Mentoria."
    assert out3["current_node"] == "END"
    assert out3["completed"] is True


async def test_routes_to_basica_when_faturamento_low() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node = {
        "saudacao": {"response_text": "Oi!"},
        "qualificacao": {"response_text": "Anotado: 5k.", "faturamento": 5000},
        "basica": {"response_text": "Te apresento a Aceleradora."},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )

    state: TalkFlowState = {
        "tenant_id": "t", "lead_id": "l", "treeflow_id": "demo",
        "treeflow_version": "0.1.0", "current_node": "saudacao",
        "collected": {}, "messages": [],
        "last_user_input": "", "last_agent_response": "", "completed": False,
    }
    s1 = await graph.ainvoke(state)
    s1["last_user_input"] = "5 mil"
    s2 = await graph.ainvoke(s1)
    assert s2["current_node"] == "basica"


async def test_stays_on_node_when_exit_condition_not_met() -> None:
    tf = TreeFlow.model_validate(DEMO_YAML)
    per_node = {
        "saudacao": {"response_text": "Oi!"},
        # qualificacao receives nothing extractable
        "qualificacao": {"response_text": "Pode repetir?", "faturamento": None},
    }
    graph = compile_treeflow(
        tf,
        tenant_llm=TENANT_LLM,
        secrets={"anthropic_key": "fake"},
        llm_factory=_stub_llm_factory(per_node),
    )
    state: TalkFlowState = {
        "tenant_id": "t", "lead_id": "l", "treeflow_id": "demo",
        "treeflow_version": "0.1.0", "current_node": "saudacao",
        "collected": {}, "messages": [],
        "last_user_input": "", "last_agent_response": "", "completed": False,
    }
    s1 = await graph.ainvoke(state)
    s1["last_user_input"] = "sei lá"
    s2 = await graph.ainvoke(s1)
    assert s2["current_node"] == "qualificacao"  # did NOT advance
    assert s2["completed"] is False
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_treeflow_compiler.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/ai_sdr/treeflow/compiler.py`**

```python
"""Compile a `TreeFlow` into a LangGraph `CompiledStateGraph`."""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from ai_sdr.llm.extractor import RESPONSE_FIELD, build_structured_model, extract
from ai_sdr.llm.factory import build_llm as _default_build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig, LLMDefaults
from ai_sdr.schemas.treeflow_yaml import NodeSpec, TreeFlow
from ai_sdr.treeflow.expressions import eval_bool
from ai_sdr.treeflow.state import Message, TalkFlowState

LLMFactory = Callable[[LLMConfig, dict[str, str], str], BaseChatModel]
"""(node_llm_cfg, secrets, current_node_id) -> BaseChatModel.

The `current_node_id` arg is purely for test stubs; the production factory ignores it."""


def _default_factory(cfg: LLMConfig, secrets: dict[str, str], _node_id: str) -> BaseChatModel:
    return _default_build_llm(cfg, secrets)


def compile_treeflow(
    tf: TreeFlow,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    llm_factory: LLMFactory | None = None,
) -> Any:
    """Compile a TreeFlow into a LangGraph StateGraph (no checkpointer attached here).

    The runtime (Task 12) attaches the postgres checkpointer with .compile(checkpointer=...).
    """
    factory: LLMFactory = llm_factory or _default_factory
    by_id = {n.id: n for n in tf.nodes}

    def _make_node_fn(node: NodeSpec):
        async def node_fn(state: TalkFlowState) -> dict[str, Any]:
            llm_cfg = node.llm or tenant_llm.default
            llm = factory(llm_cfg, secrets, node.id)

            messages = [SystemMessage(content=node.prompt)]
            for m in state.get("messages", []):
                if m["role"] == "user":
                    messages.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    from langchain_core.messages import AIMessage
                    messages.append(AIMessage(content=m["content"]))
            user_input = state.get("last_user_input", "")
            if user_input:
                messages.append(HumanMessage(content=user_input))

            model = build_structured_model(node.collects)
            result = await extract(llm, model, messages)

            extracted: dict[str, Any] = {}
            for c in node.collects:
                val = getattr(result, c.field, None)
                if val is not None:
                    extracted[c.field] = val
            collected_after = {**state.get("collected", {}), **extracted}
            response_text: str = getattr(result, RESPONSE_FIELD)

            next_node, completed = _route(node, collected_after)

            new_msgs: list[Message] = []
            if user_input:
                new_msgs.append({"role": "user", "content": user_input})
            new_msgs.append({"role": "assistant", "content": response_text})

            return {
                "collected": collected_after,
                "messages": new_msgs,
                "last_agent_response": response_text,
                "last_user_input": "",  # consumed
                "current_node": next_node,
                "completed": completed,
            }

        return node_fn

    def _route(node: NodeSpec, collected: dict[str, Any]) -> tuple[str, bool]:
        """Return (next_current_node, completed)."""
        if not _exit_satisfied(node, collected):
            return (node.id, False)
        for tr in node.next_nodes:
            if eval_bool(tr.condition, collected):
                if tr.target == "END":
                    return ("END", True)
                return (tr.target, False)
        # nothing matched — stay (operator pebcak, but don't crash)
        return (node.id, False)

    def _exit_satisfied(node: NodeSpec, collected: dict[str, Any]) -> bool:
        ec = node.exit_condition
        if ec.type == "all_fields_filled":
            return all(c.field in collected and collected[c.field] is not None for c in node.collects if c.required)
        if ec.type == "rule_expression":
            assert ec.expression is not None
            return eval_bool(ec.expression, collected)
        if ec.type == "combined":
            assert ec.expression is not None
            all_filled = all(
                c.field in collected and collected[c.field] is not None
                for c in node.collects if c.required
            )
            return all_filled and eval_bool(ec.expression, collected)
        return False

    # Build graph: START → router → <picked node> → END
    sg: StateGraph = StateGraph(TalkFlowState)

    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))

    def _start_router(state: TalkFlowState) -> str:
        nid = state.get("current_node") or tf.entry_node
        if nid == "END":
            return END
        if nid not in by_id:
            raise ValueError(f"state.current_node={nid!r} not in TreeFlow")
        return nid

    sg.add_conditional_edges(START, _start_router, {**{n.id: n.id for n in tf.nodes}, END: END})
    for n in tf.nodes:
        sg.add_edge(n.id, END)

    return sg.compile()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_treeflow_compiler.py -v`

Expected: all 3 PASS.

> Common gotchas if a test fails:
> - `state["messages"]` reducer must be `operator.add` (already set in Task 8). If LangGraph complains about overwriting a list, double-check `TalkFlowState` annotation.
> - `with_structured_output` returns a runnable that `await runnable.ainvoke()` — make sure tests don't use sync `invoke`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py tests/unit/test_treeflow_compiler.py
git commit -m "feat(plan2 t9): TreeFlow → LangGraph compiler with exit gating + transition routing"
```

---

## Task 10: Postgres checkpointer wiring

**Files:**
- Create: `src/ai_sdr/treeflow/checkpointer.py`
- Create: `tests/integration/test_checkpointer_postgres.py`

**Design:** `langgraph-checkpoint-postgres` ships an `AsyncPostgresSaver` that wants a **psycopg3 DSN** (`postgresql://...`), not a SQLAlchemy URL (`postgresql+asyncpg://...`). We add a helper that strips `+asyncpg` from the settings URL. The saver creates its tables on first `await saver.setup()`. We provide:
- `async with checkpointer_from_settings() as saver: ...` — context manager, opens & closes the psycopg pool
- `async def ensure_checkpointer_schema()` — one-shot helper to call `.setup()`

We do **not** put the checkpointer in a global singleton — each FastAPI request / CLI invocation gets a fresh `async with`. (LangGraph's saver is cheap to instantiate; it pools connections internally.)

- [ ] **Step 1: Create `src/ai_sdr/treeflow/checkpointer.py`**

```python
"""Postgres checkpointer for LangGraph (uses psycopg3, not asyncpg).

Settings's database_url is a SQLAlchemy URL (`postgresql+asyncpg://...`);
this module rewrites it to a psycopg DSN.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ai_sdr.settings import get_settings


def _to_psycopg_dsn(sqlalchemy_url: str) -> str:
    """Convert 'postgresql+asyncpg://user:pw@host:port/db' to 'postgresql://user:pw@host:port/db'."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@asynccontextmanager
async def checkpointer_from_settings() -> AsyncIterator[AsyncPostgresSaver]:
    """Yield a connected AsyncPostgresSaver built from `settings.database_url`."""
    dsn = _to_psycopg_dsn(get_settings().database_url)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        yield saver


async def ensure_checkpointer_schema() -> None:
    """Create the checkpointer's own tables (idempotent). Run once at startup."""
    async with checkpointer_from_settings() as saver:
        await saver.setup()
```

- [ ] **Step 2: Write the failing integration test**

`tests/integration/test_checkpointer_postgres.py`:

```python
import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from ai_sdr.treeflow.checkpointer import (
    checkpointer_from_settings,
    ensure_checkpointer_schema,
)


class S(TypedDict, total=False):
    count: int


@pytest.mark.integration
async def test_checkpointer_persists_state_across_invocations() -> None:
    await ensure_checkpointer_schema()

    async def bump(state: S) -> S:
        return {"count": (state.get("count") or 0) + 1}

    sg: StateGraph = StateGraph(S)
    sg.add_node("bump", bump)
    sg.add_edge(START, "bump")
    sg.add_edge("bump", END)

    async with checkpointer_from_settings() as saver:
        graph = sg.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "test-thread-checkpoint-roundtrip"}}

        out1 = await graph.ainvoke({"count": 0}, config=cfg)
        assert out1["count"] == 1

        # invoke again with same thread_id — state from checkpoint persists
        out2 = await graph.ainvoke({}, config=cfg)
        assert out2["count"] == 2

        # different thread starts fresh
        out_other = await graph.ainvoke({"count": 0}, config={"configurable": {"thread_id": "other"}})
        assert out_other["count"] == 1
```

- [ ] **Step 3: Run the test**

Run: `make up && uv run pytest tests/integration/test_checkpointer_postgres.py -v -m integration`

Expected: PASS. The first run will create the `checkpoints`/`checkpoint_writes`/`checkpoint_migrations` tables in the `ai_sdr` database.

> If you see `ModuleNotFoundError: No module named 'psycopg'`, re-run `uv sync`. The `psycopg[binary,pool]` dep installs the binary wheels; no `libpq-dev` needed in dev.

- [ ] **Step 4: Verify the tables exist**

```bash
docker exec ai_sdr_postgres psql -U ai_sdr -d ai_sdr -c "\dt"
```

Expected: see `checkpoints`, `checkpoint_writes`, `checkpoint_migrations` alongside `tenants`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/treeflow/checkpointer.py tests/integration/test_checkpointer_postgres.py
git commit -m "feat(plan2 t10): AsyncPostgresSaver wiring + setup() helper + round-trip test"
```

---

## Task 11: `treeflow_versions` and `talkflows` tables (with RLS)

**Files:**
- Create: `src/ai_sdr/models/treeflow_version.py`
- Create: `src/ai_sdr/models/talkflow.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `migrations/versions/0003_treeflow_tables.py`
- Create: `tests/integration/test_treeflow_models.py`

**Multi-tenant note (CLAUDE.md says CRÍTICO):** Both new tables include `tenant_id UUID` + RLS policy + `FORCE ROW LEVEL SECURITY`. Test asserts isolation.

- [ ] **Step 1: Create `src/ai_sdr/models/treeflow_version.py`**

```python
"""TreeFlow version snapshot — immutable record of a published TreeFlow YAML."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class TreeflowVersion(Base):
    __tablename__ = "treeflow_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "treeflow_id", "version", name="uq_tfv_tenant_id_ver"),
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
    treeflow_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex of yaml
    content_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Create `src/ai_sdr/models/talkflow.py`**

```python
"""TalkFlow — a live conversation instance traversing a TreeFlow version."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

TalkFlowStatus = Literal["active", "completed", "cold"]


class TalkFlow(Base):
    __tablename__ = "talkflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "lead_id", name="uq_talkflows_tenant_lead"),
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
    lead_id: Mapped[str] = mapped_column(String(128), nullable=False)
    treeflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("treeflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    thread_id: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "completed", "cold", name="talkflow_status"),
        nullable=False,
        server_default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 3: Update `src/ai_sdr/models/__init__.py`**

```python
"""SQLAlchemy models. Each model is re-exported here so alembic can discover them."""

from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

__all__ = ["Tenant", "TreeflowVersion", "TalkFlow"]
```

- [ ] **Step 4: Generate migration** (manually for deterministic revision id)

Create `migrations/versions/0003_treeflow_tables.py`:

```python
"""treeflow_versions + talkflows tables (with RLS)

Revision ID: 0003_treeflow_tables
Revises: 0002_tenants_table
Create Date: 2026-05-22 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_treeflow_tables"
down_revision = "0002_tenants_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "treeflow_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_yaml", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "treeflow_id", "version", name="uq_tfv_tenant_id_ver"),
    )
    op.create_index("ix_treeflow_versions_tenant_id", "treeflow_versions", ["tenant_id"])

    op.create_table(
        "talkflows",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", sa.String(length=128), nullable=False),
        sa.Column("treeflow_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(length=256), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "completed", "cold", name="talkflow_status"),
            server_default="active",
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
        sa.ForeignKeyConstraint(
            ["treeflow_version_id"], ["treeflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_talkflows_tenant_lead"),
        sa.UniqueConstraint("thread_id", name="uq_talkflows_thread_id"),
    )
    op.create_index("ix_talkflows_tenant_id", "talkflows", ["tenant_id"])
    op.create_index("ix_talkflows_thread_id", "talkflows", ["thread_id"], unique=True)

    # RLS — both tables
    for tbl in ("treeflow_versions", "talkflows"):
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
    for tbl in ("talkflows", "treeflow_versions"):
        op.execute(f"DROP POLICY IF EXISTS tenant_iso ON {tbl};")
    op.drop_index("ix_talkflows_thread_id", table_name="talkflows")
    op.drop_index("ix_talkflows_tenant_id", table_name="talkflows")
    op.drop_table("talkflows")
    op.execute("DROP TYPE IF EXISTS talkflow_status;")
    op.drop_index("ix_treeflow_versions_tenant_id", table_name="treeflow_versions")
    op.drop_table("treeflow_versions")
```

- [ ] **Step 5: Apply the migration**

Run: `make migrate`

Expected: alembic applies `0003_treeflow_tables`.

- [ ] **Step 6: Write the integration test**

`tests/integration/test_treeflow_models.py`:

```python
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings


@pytest.fixture
async def session() -> AsyncSession:
    eng = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        yield s
    await eng.dispose()


@pytest.mark.integration
async def test_create_and_read_versions_and_talkflows(session: AsyncSession) -> None:
    # superuser? if so, FORCE ROW LEVEL SECURITY in migration handles it.
    async with session.begin():
        t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T")
        session.add(t)
        await session.flush()
        await set_tenant_context(session, t.id)

        v = TreeflowVersion(
            tenant_id=t.id,
            treeflow_id="demo",
            version="0.1.0",
            content_hash="deadbeef",
            content_yaml="id: demo\nversion: 0.1.0\n",
        )
        session.add(v)
        await session.flush()

        tf = TalkFlow(
            tenant_id=t.id,
            lead_id="lead-1",
            treeflow_version_id=v.id,
            thread_id=f"{t.id}:demo:lead-1",
        )
        session.add(tf)

    async with session.begin():
        await set_tenant_context(session, t.id)
        got = (await session.execute(select(TalkFlow).where(TalkFlow.lead_id == "lead-1"))).scalar_one()
        assert got.status == "active"


@pytest.mark.integration
async def test_rls_blocks_cross_tenant_reads_on_talkflows(session: AsyncSession) -> None:
    async with session.begin():
        t1 = Tenant(slug=f"a-{uuid.uuid4().hex[:8]}", display_name="A")
        t2 = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", display_name="B")
        session.add_all([t1, t2])
        await session.flush()

        await set_tenant_context(session, t1.id)
        v1 = TreeflowVersion(
            tenant_id=t1.id, treeflow_id="d", version="0.1.0",
            content_hash="x", content_yaml="x",
        )
        session.add(v1)
        await session.flush()
        session.add(TalkFlow(
            tenant_id=t1.id, lead_id="L1",
            treeflow_version_id=v1.id, thread_id=f"{t1.id}:d:L1",
        ))

        await set_tenant_context(session, t2.id)
        v2 = TreeflowVersion(
            tenant_id=t2.id, treeflow_id="d", version="0.1.0",
            content_hash="x", content_yaml="x",
        )
        session.add(v2)
        await session.flush()
        session.add(TalkFlow(
            tenant_id=t2.id, lead_id="L2",
            treeflow_version_id=v2.id, thread_id=f"{t2.id}:d:L2",
        ))

    # Read as t1 — should see only L1
    async with session.begin():
        await set_tenant_context(session, t1.id)
        rows = (await session.execute(select(TalkFlow))).scalars().all()
        leads = sorted(r.lead_id for r in rows)
        assert leads == ["L1"]

    # Read as t2 — should see only L2
    async with session.begin():
        await set_tenant_context(session, t2.id)
        rows = (await session.execute(select(TalkFlow))).scalars().all()
        leads = sorted(r.lead_id for r in rows)
        assert leads == ["L2"]
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/integration/test_treeflow_models.py -v -m integration`

Expected: both PASS.

- [ ] **Step 8: Commit**

```bash
git add src/ai_sdr/models/treeflow_version.py src/ai_sdr/models/talkflow.py src/ai_sdr/models/__init__.py migrations/versions/0003_treeflow_tables.py tests/integration/test_treeflow_models.py
git commit -m "feat(plan2 t11): treeflow_versions + talkflows tables with RLS"
```

---

## Task 12: TalkFlowRuntime

**Files:**
- Create: `src/ai_sdr/treeflow/runtime.py`
- Create: `tests/integration/test_talkflow_runtime.py`

**Design:** The runtime is the public Python API the rest of the system (and the CLI, and later the WhatsApp webhook) uses:

```python
runtime = TalkFlowRuntime(
    tenant_loader=TenantLoader(tenants_dir),
    treeflow_loader=TreeFlowLoader(tenants_dir),
    sops_loader=SopsLoader(tenants_dir),
)
version = await runtime.publish_version(tenant_id, treeflow_id)
tf = await runtime.create(tenant_id, lead_id="lead-1", treeflow_id="mentoria")
result = await runtime.step(tenant_id, talkflow_id=tf.id, user_input="oi")  # → StepResult
```

`thread_id` convention: `f"{tenant_id}:{talkflow_id}"`. The LangGraph checkpointer keys on `thread_id` only, so we encode tenant in the prefix — combined with RLS on `talkflows`, an attacker would need to compromise both the tenant lookup AND the postgres role to leak data.

`publish_version`: read the YAML, hash sha256, upsert into `treeflow_versions`. Idempotent — re-publishing same content does nothing.

For LLM calls inside `.step()` the runtime resolves the tenant's secrets via SopsLoader. Tests can inject a fake `secrets_resolver` and a fake `llm_factory` to avoid hitting both SOPS and the real LLM.

- [ ] **Step 1: Create `src/ai_sdr/treeflow/runtime.py`**

```python
"""TalkFlowRuntime — orchestrates publish_version, create, step using all the pieces."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.llm_yaml import LLMDefaults
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import checkpointer_from_settings
from ai_sdr.treeflow.compiler import LLMFactory, compile_treeflow
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.state import TalkFlowState


@dataclass
class StepResult:
    talkflow_id: uuid.UUID
    response_text: str
    current_node: str
    completed: bool
    collected: dict[str, Any]


SecretsResolver = Callable[[str], dict[str, str]]
"""(tenant_slug) -> {secret_name: value}. Default: SopsLoader.load."""


class TalkFlowRuntime:
    def __init__(
        self,
        *,
        tenant_loader: TenantLoader,
        treeflow_loader: TreeFlowLoader,
        sops_loader: SopsLoader,
        llm_factory: LLMFactory | None = None,
        secrets_resolver: SecretsResolver | None = None,
    ) -> None:
        self._tenants = tenant_loader
        self._treeflows = treeflow_loader
        self._sops = sops_loader
        self._llm_factory = llm_factory
        self._resolve_secrets: SecretsResolver = secrets_resolver or self._sops.load

    # ---------- public ----------

    async def publish_version(
        self,
        session: AsyncSession,
        tenant: Tenant,
        treeflow_id: str,
    ) -> TreeflowVersion:
        """Snapshot tenants/<slug>/treeflows/<treeflow_id>.yaml into treeflow_versions.

        Idempotent — returns the existing row if (tenant, id, version, hash) already match.
        """
        tf = self._treeflows.load(tenant.slug, treeflow_id)
        raw = self._treeflows.raw_yaml(tenant.slug, treeflow_id)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        await set_tenant_context(session, tenant.id)
        existing = (
            await session.execute(
                select(TreeflowVersion).where(
                    TreeflowVersion.tenant_id == tenant.id,
                    TreeflowVersion.treeflow_id == treeflow_id,
                    TreeflowVersion.version == tf.version,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.content_hash != digest:
                raise ValueError(
                    f"TreeFlow {treeflow_id} v{tf.version} already published with a different hash; "
                    "bump the version field in the YAML before re-publishing."
                )
            return existing

        row = TreeflowVersion(
            tenant_id=tenant.id,
            treeflow_id=treeflow_id,
            version=tf.version,
            content_hash=digest,
            content_yaml=raw,
        )
        session.add(row)
        await session.flush()
        return row

    async def create(
        self,
        session: AsyncSession,
        tenant: Tenant,
        lead_id: str,
        treeflow_id: str,
    ) -> TalkFlow:
        """Create a TalkFlow row pinned to the latest published version of `treeflow_id`."""
        await set_tenant_context(session, tenant.id)
        version = (
            await session.execute(
                select(TreeflowVersion)
                .where(
                    TreeflowVersion.tenant_id == tenant.id,
                    TreeflowVersion.treeflow_id == treeflow_id,
                )
                .order_by(TreeflowVersion.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if version is None:
            raise ValueError(
                f"TreeFlow {treeflow_id} has no published versions for tenant {tenant.slug}; "
                "call publish_version() first."
            )

        # Reject duplicate active talkflow for same lead
        existing = (
            await session.execute(
                select(TalkFlow).where(
                    TalkFlow.tenant_id == tenant.id,
                    TalkFlow.lead_id == lead_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        new_id = uuid.uuid4()
        thread_id = f"{tenant.id}:{new_id}"
        row = TalkFlow(
            id=new_id,
            tenant_id=tenant.id,
            lead_id=lead_id,
            treeflow_version_id=version.id,
            thread_id=thread_id,
        )
        session.add(row)
        await session.flush()
        return row

    async def step(
        self,
        session: AsyncSession,
        tenant: Tenant,
        talkflow_id: uuid.UUID,
        user_input: str,
    ) -> StepResult:
        """Run one turn of the conversation. Persists state via the postgres checkpointer."""
        await set_tenant_context(session, tenant.id)
        talkflow = (
            await session.execute(
                select(TalkFlow).where(TalkFlow.id == talkflow_id)
            )
        ).scalar_one()
        version = (
            await session.execute(
                select(TreeflowVersion).where(TreeflowVersion.id == talkflow.treeflow_version_id)
            )
        ).scalar_one()

        tf = TreeFlow.model_validate(__import__("yaml").safe_load(version.content_yaml))
        tenant_cfg = self._tenants.load(tenant.slug)
        if tenant_cfg.llm is None:
            raise ValueError(f"tenant {tenant.slug} has no llm config in tenant.yaml")
        llm_defaults: LLMDefaults = tenant_cfg.llm
        secrets = self._resolve_secrets(tenant.slug)

        async with checkpointer_from_settings() as saver:
            graph = compile_treeflow(
                tf,
                tenant_llm=llm_defaults,
                secrets=secrets,
                llm_factory=self._llm_factory,
            ).with_config({"checkpointer": saver})  # newer langgraph API; if not supported, see fallback below

            # Fallback for langgraph versions that need checkpointer at compile time:
            # recompile here passing checkpointer= explicitly (kept simple — we just recompile).
            from ai_sdr.treeflow.compiler import compile_treeflow as _ct  # noqa: F401

            cfg = {"configurable": {"thread_id": talkflow.thread_id}}
            input_state: TalkFlowState = {"last_user_input": user_input}

            # Bootstrap state on first turn
            checkpoint_state = await saver.aget(cfg)  # type: ignore[attr-defined]
            if checkpoint_state is None:
                input_state = {
                    "tenant_id": str(tenant.id),
                    "lead_id": talkflow.lead_id,
                    "treeflow_id": tf.id,
                    "treeflow_version": tf.version,
                    "current_node": tf.entry_node,
                    "collected": {},
                    "messages": [],
                    "last_user_input": user_input,
                    "last_agent_response": "",
                    "completed": False,
                }

            out = await graph.ainvoke(input_state, config=cfg)

        if out.get("completed"):
            talkflow.status = "completed"
            await session.flush()

        return StepResult(
            talkflow_id=talkflow.id,
            response_text=out.get("last_agent_response", ""),
            current_node=out.get("current_node", ""),
            completed=bool(out.get("completed", False)),
            collected=out.get("collected", {}),
        )
```

> **Important:** the snippet above uses `.with_config({"checkpointer": ...})`. In the langgraph version we're pinning (>=0.2.60) the canonical API is to pass the checkpointer at `compile(checkpointer=...)` time. If `.with_config` doesn't take a checkpointer in your installed version, change `step()` to **recompile per call** with `compile_treeflow(...).compile(checkpointer=saver)`. Concretely, replace the `compile_treeflow(...).with_config(...)` line with:
>
> ```python
> from langgraph.graph import StateGraph  # for type only
> graph = _ct(tf, tenant_llm=llm_defaults, secrets=secrets, llm_factory=self._llm_factory)
> # if `graph` is already compiled, swap to passing checkpointer to .compile() in compile_treeflow.
> ```
>
> Easier fix: extend `compile_treeflow()` to accept an optional `checkpointer` arg and pass it through to `sg.compile(checkpointer=...)`. Do this **only if** the `.with_config` path doesn't work — verify by running the runtime integration test (Step 3 below).

- [ ] **Step 2: Write the integration test (FakeListChatModel path)**

`tests/integration/test_talkflow_runtime.py`:

```python
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.llm_yaml import LLMConfig
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

DEMO_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: saudacao
nodes:
  - id: saudacao
    prompt: Cumprimente.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: qualificacao
  - id: qualificacao
    prompt: Pergunte faturamento.
    collects:
      - field: faturamento
        type: number
        required: true
    exit_condition:
      type: rule_expression
      expression: "faturamento != None"
    next_nodes:
      - condition: "faturamento >= 30000"
        target: premium
      - condition: "faturamento < 30000"
        target: basica
  - id: premium
    prompt: Oferta premium.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
  - id: basica
    prompt: Oferta básica.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
"""

TENANT_YAML = """\
id: rttest
display_name: RT Test
timezone: America/Sao_Paulo
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: secrets/anthropic_key
"""


@pytest.fixture
def fake_tenants(tmp_path: Path) -> Path:
    base = tmp_path / "tenants" / "rttest"
    (base / "treeflows").mkdir(parents=True)
    (base / "tenant.yaml").write_text(TENANT_YAML)
    (base / "treeflows" / "demo.yaml").write_text(DEMO_YAML)
    return tmp_path / "tenants"


def _stub_factory(per_node_payloads: dict[str, dict[str, Any]]):
    class _Stub:
        def __init__(self, nid: str) -> None:
            self._nid = nid

        def with_structured_output(self, model: type[BaseModel]) -> Any:
            return RunnableLambda(lambda _msgs: model.model_validate(per_node_payloads[self._nid]))

    def factory(cfg: LLMConfig, secrets: dict[str, str], current_node: str) -> BaseChatModel:  # noqa: ARG001
        return _Stub(current_node)  # type: ignore[return-value]

    return factory


@pytest.mark.integration
async def test_publish_create_step_end_to_end(fake_tenants: Path) -> None:
    await ensure_checkpointer_schema()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    sops = SopsLoader(tenants_dir=fake_tenants)  # we override secrets via resolver below
    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=fake_tenants),
        treeflow_loader=TreeFlowLoader(tenants_dir=fake_tenants),
        sops_loader=sops,
        secrets_resolver=lambda _slug: {"anthropic_key": "fake"},
        llm_factory=_stub_factory({
            "saudacao": {"response_text": "Oi!"},
            "qualificacao": {"response_text": "50k anotado.", "faturamento": 50000},
            "premium": {"response_text": "Mentoria pra você."},
        }),
    )

    async with sm() as session:
        async with session.begin():
            t = Tenant(slug=f"rttest-{uuid.uuid4().hex[:6]}", display_name="RT")
            session.add(t)
            await session.flush()
            await runtime.publish_version(session, t, "demo")
            tf = await runtime.create(session, t, lead_id="lead-A", treeflow_id="demo")
        tf_id = tf.id
        tenant_id = t.id
        tenant_slug = t.slug
        # tenant_loader cache used the slug; ensure same tenant survives across sessions
        from ai_sdr.tenant_loader.loader import TenantLoader as TL
        runtime._tenants = TL(tenants_dir=fake_tenants)  # fresh cache

    async with sm() as session:
        from sqlalchemy import select
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()

        r1 = await runtime.step(session, t, tf_id, user_input="")
        assert r1.response_text == "Oi!"
        assert r1.current_node == "qualificacao"
        assert r1.completed is False

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r2 = await runtime.step(session, t, tf_id, user_input="faturo 50k")
        assert "50k" in r2.response_text.lower() or "anotado" in r2.response_text.lower()
        assert r2.collected.get("faturamento") == 50000
        assert r2.current_node == "premium"

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r3 = await runtime.step(session, t, tf_id, user_input="manda")
        assert r3.completed is True
        assert r3.current_node == "END"

    await engine.dispose()
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/integration/test_talkflow_runtime.py -v -m integration`

Expected: PASS.

> **If the checkpointer doesn't persist** (e.g., turn 2 starts from scratch): the most common cause is the `.with_config({"checkpointer": ...})` path not actually attaching the saver in your installed langgraph. Fix: extend `compile_treeflow()` in `src/ai_sdr/treeflow/compiler.py` to accept an optional `checkpointer` kwarg and pass it to `sg.compile(checkpointer=checkpointer)`. Then in `runtime.step()` replace the `.with_config(...)` block with:
>
> ```python
> graph = compile_treeflow(
>     tf, tenant_llm=llm_defaults, secrets=secrets,
>     llm_factory=self._llm_factory, checkpointer=saver,
> )
> out = await graph.ainvoke(input_state, config=cfg)
> ```

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/treeflow/runtime.py tests/integration/test_talkflow_runtime.py
git commit -m "feat(plan2 t12): TalkFlowRuntime — publish_version + create + step (postgres-persisted)"
```

---

## Task 13: Alembic stamp for checkpointer (documentation migration)

**Files:**
- Create: `migrations/versions/0004_checkpointer_setup.py`

**Why a migration that does nothing?** It records intent. Anyone reading `migrations/versions/` sees that, by revision 0004, the database is expected to contain the checkpointer tables. The actual DDL is owned by langgraph-checkpoint-postgres (called via `ensure_checkpointer_schema()` from app/CLI startup). The migration body is just an `op.execute(...)` block that confirms the lib is responsible.

- [ ] **Step 1: Create `migrations/versions/0004_checkpointer_setup.py`**

```python
"""checkpointer schema is created by langgraph-checkpoint-postgres at startup (no-op stamp)

Revision ID: 0004_checkpointer_setup
Revises: 0003_treeflow_tables
Create Date: 2026-05-22 00:00:00
"""


revision = "0004_checkpointer_setup"
down_revision = "0003_treeflow_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: the `checkpoints`, `checkpoint_writes`, `checkpoint_migrations` tables
    are created by `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.setup()`,
    invoked from `ai_sdr.treeflow.checkpointer.ensure_checkpointer_schema()` at app
    startup. This stamp records that, by revision 0004, those tables are expected
    to exist."""


def downgrade() -> None:
    pass
```

- [ ] **Step 2: Apply** (just bumps alembic_version)

Run: `make migrate`

Expected: alembic reports `Running upgrade 0003_treeflow_tables -> 0004_checkpointer_setup`.

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0004_checkpointer_setup.py
git commit -m "chore(plan2 t13): document checkpointer schema ownership with alembic stamp"
```

---

## Task 14: Simulate CLI

**Files:**
- Create: `src/ai_sdr/cli/__init__.py` (empty)
- Create: `src/ai_sdr/cli/app.py`
- Create: `src/ai_sdr/cli/simulate.py`
- Create: `tests/integration/test_simulate_cli.py`

**Behavior:** `ai-sdr simulate --tenant <slug> --treeflow <id> --lead <id>` opens a REPL:
- On first turn it prints the agent's greeting (the entry Node's response).
- Each user line is fed via `runtime.step()`.
- Each agent response is printed prefixed with `[node:<id>] >`.
- Extracted fields are printed in dim if `--show-extracted`.
- Commands: `/quit` exits, `/state` prints accumulated state, `/restart` deletes the TalkFlow row + checkpoints and starts over.

The CLI uses the **same TalkFlowRuntime** as the production code path, just bound to a real Postgres (via settings) and real LLMs (via SopsLoader → tenant's encrypted `anthropic_key` / `openai_key`).

- [ ] **Step 1: Create empty `src/ai_sdr/cli/__init__.py`**

```python
```

- [ ] **Step 2: Create `src/ai_sdr/cli/app.py`**

```python
"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.simulate import simulate

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)


if __name__ == "__main__":  # pragma: no cover
    app()
```

- [ ] **Step 3: Create `src/ai_sdr/cli/simulate.py`**

```python
"""Interactive REPL for stepping a TalkFlow against a real LLM."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime


def simulate(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug (must exist in DB and tenants/<slug>/)")],
    treeflow: Annotated[str, typer.Option("--treeflow", help="TreeFlow id (yaml filename without .yaml)")],
    lead: Annotated[str, typer.Option("--lead", help="Lead identifier (free-form; per-tenant unique)")],
    show_extracted: Annotated[bool, typer.Option("--show-extracted/--no-show-extracted")] = False,
    tenants_dir: Annotated[Path, typer.Option("--tenants-dir")] = Path("tenants"),
) -> None:
    """Run a TalkFlow in the terminal — real Postgres, real LLM, no WhatsApp/CRM."""
    asyncio.run(_run(tenant, treeflow, lead, show_extracted, tenants_dir))


async def _run(tenant_slug: str, treeflow_id: str, lead_id: str, show_extracted: bool, tenants_dir: Path) -> None:
    await ensure_checkpointer_schema()

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=tenants_dir),
        treeflow_loader=TreeFlowLoader(tenants_dir=tenants_dir),
        sops_loader=SopsLoader(tenants_dir=tenants_dir),
    )

    async with sm() as session:
        async with session.begin():
            t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
            if t is None:
                typer.secho(
                    f"tenant {tenant_slug!r} not found in DB — INSERT INTO tenants (slug, display_name) ...",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(code=1)
            await runtime.publish_version(session, t, treeflow_id)
            tf = await runtime.create(session, t, lead_id=lead_id, treeflow_id=treeflow_id)
        tf_id = tf.id
        tenant_slug_final = t.slug

    typer.secho(f"[talkflow:{tf_id}] type a message, /quit to exit.\n", fg=typer.colors.GREEN)

    # First turn — empty user input lets the entry node greet
    user_msg = ""
    while True:
        async with sm() as session:
            t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug_final))).scalar_one()
            result = await runtime.step(session, t, tf_id, user_input=user_msg)

        typer.secho(f"[node:{result.current_node}] > {result.response_text}", fg=typer.colors.CYAN)
        if show_extracted and result.collected:
            typer.secho(f"  collected: {result.collected}", fg=typer.colors.BRIGHT_BLACK)
        if result.completed:
            typer.secho("\n[talkflow completed]", fg=typer.colors.GREEN)
            break

        try:
            user_msg = typer.prompt("you", default="", show_default=False)
        except (KeyboardInterrupt, EOFError):
            break
        if user_msg.strip() == "/quit":
            break
        if user_msg.strip() == "/restart":
            async with sm() as session:
                t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug_final))).scalar_one()
                await session.execute(
                    TalkFlow.__table__.delete().where(TalkFlow.id == tf_id)
                )
                await session.commit()
            typer.secho("[restarted — exiting; re-run the command]", fg=typer.colors.YELLOW)
            break

    await engine.dispose()
```

- [ ] **Step 4: Write the smoke test**

`tests/integration/test_simulate_cli.py`:

```python
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings


# This test invokes `ai-sdr simulate` via subprocess against a real DB + real LLM (Anthropic).
# It is marked `live_llm` and `integration` so default test runs skip it.

DEMO_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: saudacao
nodes:
  - id: saudacao
    prompt: Diga apenas "olá" e nada mais.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
"""


@pytest.mark.integration
@pytest.mark.live_llm
async def test_simulate_cli_smoke(tmp_path: Path) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    tenants_dir = tmp_path / "tenants" / "smoke"
    (tenants_dir / "treeflows").mkdir(parents=True)
    (tenants_dir / "tenant.yaml").write_text(
        yaml.safe_dump({
            "id": "smoke",
            "display_name": "Smoke",
            "timezone": "America/Sao_Paulo",
            "llm": {
                "default": {
                    "provider": "anthropic",
                    "model": "claude-haiku-4-5",
                    "api_key_ref": "secrets/anthropic_key",
                }
            },
        })
    )
    (tenants_dir / "treeflows" / "demo.yaml").write_text(DEMO_YAML)

    # SOPS is bypassed by setting a fake encrypted file we'll never decrypt — the runtime
    # uses sops_loader.load(slug); to keep this test pure-subprocess, we set the env var
    # the sops binary reads. Simpler: write a plain (unencrypted) yaml under .enc.yaml
    # and trust that the SopsLoader will fall back? No — sops will reject. Instead, we
    # use a custom runner via Python -c that monkeypatches SopsLoader.load.

    # Insert tenant row
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        async with s.begin():
            t = Tenant(slug="smoke", display_name="Smoke")
            s.add(t)

    code = f"""
import os, asyncio
from ai_sdr.cli.simulate import _run
from ai_sdr.secrets.sops_loader import SopsLoader

# inject anthropic key from env into SopsLoader (simulate CLI uses real SopsLoader by default)
SopsLoader.load = lambda self, _slug: {{"anthropic_key": os.environ["ANTHROPIC_API_KEY"]}}

asyncio.run(_run(
    tenant_slug="smoke", treeflow_id="demo", lead_id="smoke-lead-1",
    show_extracted=False, tenants_dir=__import__("pathlib").Path({str(tenants_dir)!r}),
))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        env={**os.environ},
        input="/quit\n",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "olá" in result.stdout.lower() or "ola" in result.stdout.lower()
    await engine.dispose()
```

- [ ] **Step 5: Verify the CLI is registered**

Run: `uv sync && uv run ai-sdr --help`

Expected: typer prints `Usage: ai-sdr [OPTIONS] COMMAND [ARGS]...` and lists `simulate`.

- [ ] **Step 6: Run the smoke test** (only if you have ANTHROPIC_API_KEY)

Run: `ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/integration/test_simulate_cli.py -v -m live_llm`

Expected: PASS, with "olá" appearing in captured stdout.

(If you don't have a key today, run `uv run pytest tests/integration/test_simulate_cli.py -v -m integration` — it should be skipped.)

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/cli/__init__.py src/ai_sdr/cli/app.py src/ai_sdr/cli/simulate.py tests/integration/test_simulate_cli.py
git commit -m "feat(plan2 t14): ai-sdr simulate CLI (typer-based interactive REPL)"
```

---

## Task 15: Example tenant + TreeFlow fixture

**Files:**
- Modify: `tenants/example/tenant.yaml`
- Modify: `tenants/example/secrets.enc.yaml` (re-encrypt with new keys)
- Create: `tenants/example/treeflows/example.yaml`

- [ ] **Step 1: Update `tenants/example/tenant.yaml`**

Replace the file contents:

```yaml
id: "example"
display_name: "Example Tenant"
timezone: "America/Sao_Paulo"

schedule:
  mon-fri: "08:00-22:00"
  sat: "09:00-18:00"
  sun: "off"
  off_hours_behavior: "queue"

conversation:
  debounce_ms: 5000
  optout_stop_words: ["para", "pare", "parar", "stop", "sair"]
  optout_action: "end_conversation_silent"

llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    temperature: 0.7
    api_key_ref: secrets/anthropic_key
  classifier:
    provider: anthropic
    model: claude-haiku-4-5
    api_key_ref: secrets/anthropic_key
```

- [ ] **Step 2: Create `tenants/example/treeflows/example.yaml`**

```yaml
id: example
version: 0.1.0
display_name: "Funil Exemplo"

entry_node: saudacao

nodes:
  - id: saudacao
    prompt: |
      Você é uma SDR conversando no WhatsApp em PT-BR.
      Cumprimente o lead em UMA frase curta e diga que vai fazer uma pergunta rápida.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: qualificacao

  - id: qualificacao
    prompt: |
      Você é uma SDR. Em UMA pergunta, pergunte qual é o faturamento mensal aproximado da empresa do lead, em reais.
      Se ele já disse o valor, agradeça em uma frase e siga.
    collects:
      - field: faturamento_mensal
        type: number
        extraction_hint: "valor mensal em R$, em número (sem 'R$' nem 'mil')"
        required: true
    exit_condition:
      type: rule_expression
      expression: "faturamento_mensal != None"
    next_nodes:
      - condition: "faturamento_mensal >= 30000"
        target: oferta_premium
      - condition: "faturamento_mensal < 30000"
        target: oferta_basica

  - id: oferta_premium
    prompt: |
      Apresente a Mentoria (R$ 6000) em UMA frase, e pergunte se ele tem interesse em conversar com um humano.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END

  - id: oferta_basica
    prompt: |
      Apresente a Aceleradora (R$ 1497) em UMA frase, e pergunte se ele tem interesse em conversar com um humano.
    exit_condition: { type: all_fields_filled }
    next_nodes:
      - condition: "true"
        target: END
```

- [ ] **Step 3: Re-encrypt `secrets.enc.yaml` with the LLM keys**

```bash
sops --decrypt tenants/example/secrets.enc.yaml > /tmp/example.plain.yaml
# Edit /tmp/example.plain.yaml — add (or update) the keys:
#   anthropic_key: "sk-ant-FAKE-FOR-TEST-ONLY"
#   openai_key: "sk-openai-FAKE-FOR-TEST-ONLY"
# (keep the existing fake keys for the SopsLoader test — they don't hit any real API)

cat /tmp/example.plain.yaml
# verify it has anthropic_key + openai_key

sops --encrypt /tmp/example.plain.yaml > tenants/example/secrets.enc.yaml
rm /tmp/example.plain.yaml

# Verify
sops --decrypt tenants/example/secrets.enc.yaml
```

Expected: prints the plaintext with both keys.

> If you previously created `secrets.enc.yaml` without `anthropic_key`, the existing SopsLoader test will keep passing — but adding the key now makes the example tenant usable by `ai-sdr simulate` without modification.

- [ ] **Step 4: Sanity-check by loading via Python**

```bash
uv run python -c "
from pathlib import Path
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.loader import TreeFlowLoader
cfg = TenantLoader(Path('tenants')).load('example')
print('tenant ok:', cfg.id, cfg.llm and cfg.llm.default.model)
tf = TreeFlowLoader(Path('tenants')).load('example', 'example')
print('treeflow ok:', tf.id, len(tf.nodes), 'nodes')
"
```

Expected:
```
tenant ok: example claude-sonnet-4-6
treeflow ok: example 4 nodes
```

- [ ] **Step 5: Commit**

```bash
git add tenants/example/tenant.yaml tenants/example/secrets.enc.yaml tenants/example/treeflows/example.yaml
git commit -m "feat(plan2 t15): example tenant with llm config + 4-node demo TreeFlow"
```

---

## Task 16: Live LLM round-trip test (Anthropic + OpenAI)

**Files:**
- Create: `tests/integration/test_talkflow_runtime_live.py`

Marked `live_llm` — skipped unless `ANTHROPIC_API_KEY` (and optionally `OPENAI_API_KEY`) is set. This is the test that proves the whole stack works end-to-end with real providers — but it costs ~$0.001 per run on Haiku, so we don't run it on every CI invocation.

- [ ] **Step 1: Create the test**

`tests/integration/test_talkflow_runtime_live.py`:

```python
import os
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.models.tenant import Tenant
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime

TENANT_YAML_TEMPLATE = """\
id: livetest
display_name: Live Test
timezone: America/Sao_Paulo
llm:
  default:
    provider: {provider}
    model: {model}
    api_key_ref: secrets/{secret_name}
"""

DEMO_YAML = """\
id: demo
version: 0.1.0
display_name: Demo
entry_node: ask
nodes:
  - id: ask
    prompt: |
      Você é uma SDR em PT-BR. Em UMA pergunta curta, pergunte ao lead qual é
      o faturamento mensal aproximado da empresa dele, em reais. Se ele já
      respondeu com um número, agradeça em uma frase e siga.
    collects:
      - field: faturamento
        type: number
        extraction_hint: "número em R$"
        required: true
    exit_condition:
      type: rule_expression
      expression: "faturamento != None"
    next_nodes:
      - condition: "true"
        target: END
"""


def _make_fixture(tmp_path: Path, provider: str, model: str, secret_name: str) -> Path:
    base = tmp_path / "tenants" / "livetest"
    (base / "treeflows").mkdir(parents=True)
    (base / "tenant.yaml").write_text(
        TENANT_YAML_TEMPLATE.format(provider=provider, model=model, secret_name=secret_name)
    )
    (base / "treeflows" / "demo.yaml").write_text(DEMO_YAML)
    return tmp_path / "tenants"


async def _run_e2e(tenants_dir: Path, secret_name: str, api_key: str) -> None:
    await ensure_checkpointer_schema()
    engine = create_async_engine(get_settings().database_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tenants_dir=tenants_dir),
        treeflow_loader=TreeFlowLoader(tenants_dir=tenants_dir),
        sops_loader=SopsLoader(tenants_dir=tenants_dir),
        secrets_resolver=lambda _slug: {secret_name: api_key},
    )

    async with sm() as session:
        async with session.begin():
            t = Tenant(slug=f"livetest-{uuid.uuid4().hex[:6]}", display_name="Live")
            session.add(t)
            await session.flush()
            await runtime.publish_version(session, t, "demo")
            tf = await runtime.create(session, t, lead_id=f"lead-{uuid.uuid4().hex[:6]}", treeflow_id="demo")
        tf_id, tenant_slug = tf.id, t.slug

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r1 = await runtime.step(session, t, tf_id, user_input="")
        assert r1.response_text.strip() != ""
        assert r1.current_node == "ask"

    async with sm() as session:
        t = (await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one()
        r2 = await runtime.step(session, t, tf_id, user_input="faturo cerca de 50 mil por mês")
        assert r2.collected.get("faturamento") is not None
        # the LLM should extract roughly 50000 (allow some slack — could be 50000 or 50_000)
        f = float(r2.collected["faturamento"])
        assert 40_000 <= f <= 60_000, f"unexpected faturamento extraction: {f}"
        assert r2.completed is True

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.live_llm
async def test_live_anthropic(tmp_path: Path) -> None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    tenants_dir = _make_fixture(tmp_path, "anthropic", "claude-haiku-4-5", "anthropic_key")
    await _run_e2e(tenants_dir, "anthropic_key", key)


@pytest.mark.integration
@pytest.mark.live_llm
async def test_live_openai(tmp_path: Path) -> None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    tenants_dir = _make_fixture(tmp_path, "openai", "gpt-4o-mini", "openai_key")
    await _run_e2e(tenants_dir, "openai_key", key)
```

- [ ] **Step 2: Run with at least Anthropic key** (skip if you'd rather not spend money)

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/integration/test_talkflow_runtime_live.py::test_live_anthropic -v -m live_llm
```

Expected: PASS within ~10 seconds. Cost: ~$0.001 on Haiku.

If you have OpenAI:
```bash
OPENAI_API_KEY=sk-... uv run pytest tests/integration/test_talkflow_runtime_live.py::test_live_openai -v -m live_llm
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_talkflow_runtime_live.py
git commit -m "test(plan2 t16): live LLM round-trip tests (anthropic + openai, gated by env)"
```

---

## Task 17: Lifespan integration + CLAUDE.md update + smoke

**Files:**
- Modify: `src/ai_sdr/main.py` (lifespan calls `ensure_checkpointer_schema()`)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Edit `src/ai_sdr/main.py`**

Add the import:

```python
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
```

In the `lifespan` function, after `configure_logging(...)`, add:

```python
    await ensure_checkpointer_schema()
    log.info("checkpointer.ready")
```

So the lifespan reads:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=get_settings().log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=get_settings().app_env)
    await ensure_checkpointer_schema()
    log.info("checkpointer.ready")
    yield
    log.info("app.stopping")
```

- [ ] **Step 2: Edit `CLAUDE.md` — append new sections at the end**

```markdown

## TreeFlow authoring (Plan 2)

- Place per-tenant TreeFlow YAMLs under `tenants/<slug>/treeflows/<id>.yaml`.
- Validate locally: `uv run python -c "from pathlib import Path; from ai_sdr.treeflow.loader import TreeFlowLoader; TreeFlowLoader(Path('tenants')).load('<slug>', '<id>')"`.
- After editing, bump `version` (semver). The runtime refuses to re-publish a different YAML under the same version (sha256 mismatch raises).
- Transition expressions use `simpleeval`. Allowed: comparisons, `and/or/not`, `in`, `is_set('field')`, literals, `true`/`false`. Forbidden: function calls (other than `is_set`), attribute access, dunders.
- Exit conditions:
  - `all_fields_filled` — all `collects[].required` fields have non-None values
  - `rule_expression` — provide `expression: "<expr>"`; evaluates against `collected`
  - `combined` — both must hold
- Forward-compat fields on a NodeSpec (accepted but unused until later plans): `knowledge_base`, `handles_objections`, `sync_to_crm`, `critical`.

## TalkFlow runtime

- Engine API: `ai_sdr.treeflow.runtime.TalkFlowRuntime`. Methods: `publish_version` / `create` / `step`.
- `thread_id = f"{tenant_id}:{talkflow_id}"`. LangGraph's checkpointer keys on `thread_id`; tenant safety comes from (a) RLS on `talkflows`, (b) this prefix convention enforced by `create()`.
- One LLM call per `.step()`. Plan 2 has no retry; Plan 8 will add backoff.

## Simulate CLI

```bash
# 1. Insert the tenant row (one-time)
docker exec -it ai_sdr_postgres psql -U ai_sdr -d ai_sdr \
  -c "INSERT INTO tenants (slug, display_name) VALUES ('example', 'Example');"

# 2. Make sure tenants/example/secrets.enc.yaml has anthropic_key with a real key

# 3. Run
uv run ai-sdr simulate --tenant example --treeflow example --lead test-lead-1 --show-extracted
# Press Enter on the first prompt to let the agent greet, then chat normally.
# /quit  exits, /restart deletes and starts over
```

## Checkpointer notes

- LangGraph's checkpointer tables (`checkpoints`, `checkpoint_writes`, `checkpoint_migrations`) are created at app/CLI startup via `ensure_checkpointer_schema()`. They are NOT managed by alembic (revision 0004 is a documentation stamp only).
- Those tables have NO `tenant_id` column and NO RLS. Tenant isolation relies on:
  1. `thread_id` always prefixed with `tenant_id:` (enforced by `TalkFlowRuntime.create`)
  2. RLS on `talkflows` (the lookup table from `talkflow_id` → `thread_id`)
- To wipe checkpoints for a fresh dev run: `docker exec ai_sdr_postgres psql -U ai_sdr -d ai_sdr -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_migrations;"`
```

- [ ] **Step 3: Run the full test suite**

```bash
make lint
uv run ruff format --check .
make type
make test-unit
uv run pytest tests/integration -v -m integration -k "not live_llm"
```

Expected:
- lint, format, type: clean
- unit: all pass
- integration (no live_llm): all pass (~10-20 tests)

If you have an Anthropic key:

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest tests/integration -v -m live_llm
```

Expected: live tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/main.py CLAUDE.md
git commit -m "feat(plan2 t17): lifespan checkpointer setup + CLAUDE.md TreeFlow/CLI docs"
```

---

## Task 18: Final smoke (clean-state walk-through)

Verification only — no new code.

- [ ] **Step 1: Reset state**

```bash
make down
docker volume rm pesdr_postgres_data pesdr_redis_data 2>/dev/null || true
# (volume names match docker-compose project; if they differ, list with `docker volume ls`)
```

- [ ] **Step 2: Bring up + migrate**

```bash
make up
sleep 5
make migrate
```

Expected: alembic applies 0001 → 0002 → 0003 → 0004 cleanly.

- [ ] **Step 3: Run full test suite (skip live LLM)**

```bash
make test-unit
uv run pytest tests/integration -v -m integration -k "not live_llm"
```

Expected: all pass.

- [ ] **Step 4: Insert example tenant and live-LLM smoke** (optional, requires key)

```bash
docker exec -i ai_sdr_postgres psql -U ai_sdr -d ai_sdr <<EOF
INSERT INTO tenants (slug, display_name) VALUES ('example', 'Example')
  ON CONFLICT (slug) DO NOTHING;
EOF

# Drop a real anthropic key into the example tenant
sops --decrypt tenants/example/secrets.enc.yaml > /tmp/ex.plain.yaml
# Edit anthropic_key in /tmp/ex.plain.yaml to be your real key
sops --encrypt /tmp/ex.plain.yaml > tenants/example/secrets.enc.yaml
rm /tmp/ex.plain.yaml

uv run ai-sdr simulate --tenant example --treeflow example --lead smoke-$(date +%s) --show-extracted
# Press Enter, then answer the agent's question with "faturo 50 mil por mês"
# Verify: agent greets → asks faturamento → extracts ~50000 → presents Mentoria → completes
```

> **Important**: after smoke, revert the secrets file or rotate the key — don't commit a real key. If you encrypted with your local age recipient (which you did per Plan 1 Task 11), the file is safe to commit ENCRYPTED, but only the holder of the matching age private key can decrypt. **Make sure `.sops.yaml` doesn't accidentally include unintended recipients.**

- [ ] **Step 5: Tag the milestone**

```bash
git tag plan2-treeflow-engine-complete
git log --oneline | head -25
```

Expected: clean linear history of the 17 task commits, ending with the lifespan/docs commit.

---

## What this plan deliberately does NOT include

- **KB / pgvector retriever** — Plan 3. NodeSpec accepts `knowledge_base` but compiler ignores it.
- **Guardrails** (whitelist, critic pass) — Plan 3. NodeSpec accepts `critical` but compiler ignores it.
- **Objection classifier** — Plan 4. NodeSpec accepts `handles_objections` but compiler ignores it.
- **CRM adapter / RDStation** — Plan 5. NodeSpec accepts `sync_to_crm` but compiler ignores it.
- **WhatsApp / messaging adapters** — Plan 6.
- **Media** (Whisper / Vision / ElevenLabs) — Plan 7.
- **Follow-up scheduler** — Plan 8. Schema accepts `follow_up` but no worker exists.
- **TreeFlow soft/hard migration logic** — V2 of the product (spec §10).
- **TreeFlow versioning UI / git-tag mode** — V2.
- **`llm_judge` exit condition type** — spec §5.2 lists it; deferred (needs another LLM call). Schema only allows `all_fields_filled | rule_expression | combined`.
- **Per-node `response_format: audio`** — spec §11; Plan 7.
- **Prompt caching tuning** — spec §12. LangChain's ChatAnthropic uses prompt caching automatically for system prompts ≥1024 tokens; we don't tune it in this plan. Plan 3 or 8 will revisit.
- **Multi-provider beyond Anthropic + OpenAI** — Gemini/DeepSeek/Mistral/Ollama land in later plans (one case in factory + 1 dep each).

After completing this plan, the engine is real: declarative TreeFlow YAMLs compile into LangGraph graphs that talk to real LLMs, extract structured fields, traverse nodes by rule, and persist state across processes — all wrapped behind a Python API (`TalkFlowRuntime`) ready to be called by the WhatsApp webhook (Plan 6).
