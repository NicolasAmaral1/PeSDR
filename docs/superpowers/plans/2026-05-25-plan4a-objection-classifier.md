# Plano 4a — Objection Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Objection Classifier described in spec §4.4 — before each main LLM call in a TreeFlow Node, run a cheap Haiku classifier that detects whether the lead message raised one of the declared objections (`handles_objections` per Node + `global_objections` per TreeFlow). If detected, deflect to either an inline response (default — reuses Node persona + objection KB + "don't advance" instruction) or to a referenced sub-node (opt-in via `as_subnode`). After responding, the conversation returns to the original Node without mutating `collects`/`exit_condition` for that turn.

**Architecture:** Classifier-as-edge-router. Each TreeFlow `NodeSpec` N compiles into up to 3 LangGraph nodes: `N:classifier` (always emitted, passthrough when no applicable objections or `tenant.objections.enabled=false`), `N:inline` (emitted when N has any inline objections), and `N` (the existing Plan 2/3 main node, unchanged). `_start_router` routes `state.current_node` → `f"{current_node}:classifier"`. Sub-node mode uses sentinel `BACK_TO_ORIGIN` in the sub-node's transitions, resolved inside `_route` via `state._origin_node_id`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Postgres + pgvector (no schema change), LangGraph + langgraph-checkpoint-postgres, LangChain (Anthropic Haiku via `tenant.llm.classifier`), structlog, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-24-plan4a-objection-classifier-design.md`

---

## File structure

**New files (5):**

- `src/ai_sdr/treeflow/classifier.py` — `ClassifierResult` Pydantic model + `classify()` async function (single Haiku call via `with_structured_output`).
- `src/ai_sdr/treeflow/objection_response.py` — `build_inline_objection_messages()` — reuses `build_system_messages` pattern to assemble persona + objection prefix + KB.
- `tests/unit/test_treeflow_classifier.py` — unit tests for `classify()` with mocked LLM.
- `tests/unit/test_treeflow_objection_response.py` — unit tests for message builder.
- `kb/example/kb_obj_tempo.md`, `kb/example/kb_obj_pensar.md` — KB fixtures (markdown).

**Modified files (8):**

- `src/ai_sdr/schemas/treeflow_yaml.py` — `NodeObjection` (replaces `dict[str, Any]` opaque), `GlobalObjection` gets `description` + `as_subnode`, graph validator recognizes `BACK_TO_ORIGIN` sentinel and `as_subnode` references.
- `src/ai_sdr/schemas/tenant_yaml.py` — adds `ObjectionsConfig` + `TenantConfig.objections` field.
- `src/ai_sdr/treeflow/state.py` — adds `ObjectionRecord` TypedDict + 4 new fields on `TalkFlowState`.
- `src/ai_sdr/treeflow/compiler.py` — entry-point routing change; emits `:classifier` and `:inline` synthetic LangGraph nodes; `BACK_TO_ORIGIN` resolution inside `_route`.
- `src/ai_sdr/cli/simulate.py` — `--no-classifier` flag; include classifier result in `--show-extracted` output.
- `tenants/example/treeflows/example.yaml` — adds `global_objections` + `handles_objections` in `qualificacao` node.
- `tenants/example/tenant.yaml` — adds `objections:` block.
- `CLAUDE.md` — authoring guide section for objections.

**New test files (4):**

- `tests/integration/test_objection_runtime.py` — mocked LLM, real checkpointer.
- `tests/integration/test_objection_isolation.py` — RLS + version upgrade.
- `tests/integration/test_objection_live.py` — `live_llm` marker, real Haiku.
- `tests/integration/test_simulate_with_objections.py` — scripted simulate run.

Existing test files extended (not new): `tests/unit/test_treeflow_yaml_schema.py`, `tests/unit/test_tenant_yaml_schema.py`, `tests/unit/test_treeflow_compiler.py`.

---

## Synthetic node-name convention

`f"{node_id}:classifier"` and `f"{node_id}:inline"`. Colon is forbidden by `NODE_ID_RE`, so collision with user-defined node ids is impossible. `state.current_node` always holds a real TreeFlow node id (never the synthetic names) — synthetic names are LangGraph internals only.

---

## Task list

- T1: Schema — `NodeObjection` + tighten `GlobalObjection`
- T2: TreeFlow validator — `as_subnode` references + `BACK_TO_ORIGIN` sentinel
- T3: Tenant schema — `ObjectionsConfig`
- T4: State — `ObjectionRecord` + new TalkFlowState fields
- T5: Classifier module — `ClassifierResult` + `classify()`
- T6: Objection response builder — `build_inline_objection_messages`
- T7: Compiler — entry-point routing to `:classifier`
- T8: Compiler — emit `:classifier` synthetic node with skip + classify + dispatch
- T9: Compiler — emit `:inline` synthetic node
- T10: Compiler — `BACK_TO_ORIGIN` resolution in `_route`
- T11: Integration test — full turn cycle with mocked classifier
- T12: Integration test — sub-node mode
- T13: Integration test — cross-tenant isolation + version upgrade
- T14: Example tenant scaffolding (YAMLs + KB md files)
- T15: CLI — `--no-classifier` flag + classifier result in `--show-extracted`
- T16: Live Haiku test (`live_llm` marker)
- T17: Simulate acceptance test
- T18: CLAUDE.md authoring docs

---

## Task 1: Schema — `NodeObjection` + tighten `GlobalObjection`

**Files:**
- Modify: `src/ai_sdr/schemas/treeflow_yaml.py` (NodeObjection new class; GlobalObjection gains description + as_subnode; NodeSpec.handles_objections type tightens)
- Modify: `tests/unit/test_treeflow_yaml_schema.py` (extend with NodeObjection tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_treeflow_yaml_schema.py`:

```python
import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import (
    GlobalObjection,
    NodeObjection,
    NodeSpec,
    TreeFlow,
)


def test_node_objection_requires_description():
    with pytest.raises(ValidationError) as exc:
        NodeObjection(id="preco", kb="kb_obj_preco")
    assert "description" in str(exc.value)


def test_node_objection_description_min_length():
    with pytest.raises(ValidationError):
        NodeObjection(id="preco", kb="kb_obj_preco", description="curto")


def test_node_objection_accepts_as_subnode_optional():
    obj = NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas baratas",
    )
    assert obj.as_subnode is None

    obj2 = NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas baratas",
        as_subnode="obj_preco_node",
    )
    assert obj2.as_subnode == "obj_preco_node"


def test_global_objection_requires_description():
    with pytest.raises(ValidationError) as exc:
        GlobalObjection(id="preco", kb="kb_obj_preco")
    assert "description" in str(exc.value)


def test_node_spec_handles_objections_typed():
    node = NodeSpec(
        id="qualif",
        prompt="x",
        exit_condition={"type": "all_fields_filled"},
        next_nodes=[{"condition": "true", "target": "END"}],
        handles_objections=[
            {
                "id": "preco",
                "kb": "kb_obj_preco",
                "description": "Lead acha que está muito caro ou compara com concorrentes",
            }
        ],
    )
    assert isinstance(node.handles_objections[0], NodeObjection)
    assert node.handles_objections[0].id == "preco"


def test_node_spec_handles_objections_defaults_empty_list():
    node = NodeSpec(
        id="qualif",
        prompt="x",
        exit_condition={"type": "all_fields_filled"},
        next_nodes=[{"condition": "true", "target": "END"}],
    )
    assert node.handles_objections == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_treeflow_yaml_schema.py -v -k "objection"
```

Expected: FAIL — `NodeObjection` not importable; `GlobalObjection` accepts missing `description`.

- [ ] **Step 3: Edit `src/ai_sdr/schemas/treeflow_yaml.py`**

Replace the existing `GlobalObjection` class and remove the `Any` import line if no longer used:

```python
class GlobalObjection(BaseModel):
    """TreeFlow-level objection (matches by id against classifier output)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = None  # node_id in same TreeFlow


class NodeObjection(BaseModel):
    """Per-Node objection ref. Replaces the dict[str, Any] forward-compat blob."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kb: str = Field(min_length=1)
    description: str = Field(min_length=10, max_length=300)
    as_subnode: str | None = None  # node_id in same TreeFlow
```

In `NodeSpec`, replace:

```python
    # forward-compat — accepted but unused in plan 2
    handles_objections: list[dict[str, Any]] | None = None
```

with:

```python
    handles_objections: list[NodeObjection] = Field(default_factory=list)
```

Also remove the now-unused `Any` import if nothing else in the module uses it (check `sync_to_crm: str | None = None` and `critical: bool = False` remain). Run `grep "Any" src/ai_sdr/schemas/treeflow_yaml.py` after editing.

Update the module docstring (lines 1-11) — replace `- handles_objections (Plan 4 — classifier)` with `- handles_objections is fully implemented in Plan 4a (objection classifier).`

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_treeflow_yaml_schema.py -v
```

Expected: all PASS. Make sure no pre-existing tests broke.

- [ ] **Step 5: Lint, format, typecheck**

```bash
make lint && make format && make type
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/schemas/treeflow_yaml.py tests/unit/test_treeflow_yaml_schema.py
git commit -m "feat(plan4a t1): NodeObjection + GlobalObjection.description (typed schema, was dict opaco)

Plan 4a Task 1"
```

---

## Task 2: TreeFlow validator — `as_subnode` references + `BACK_TO_ORIGIN` sentinel

**Files:**
- Modify: `src/ai_sdr/schemas/treeflow_yaml.py` — extend `_validate_graph_consistency`
- Modify: `tests/unit/test_treeflow_yaml_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_treeflow_yaml_schema.py`:

```python
def test_as_subnode_must_reference_existing_node():
    with pytest.raises(ValidationError) as exc:
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="a",
            nodes=[
                NodeSpec(
                    id="a",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                    handles_objections=[
                        {
                            "id": "preco",
                            "kb": "k",
                            "description": "Lead questiona o valor do investimento sempre",
                            "as_subnode": "nonexistent_node",
                        }
                    ],
                ),
            ],
        )
    assert "nonexistent_node" in str(exc.value)
    assert "as_subnode" in str(exc.value)


def test_global_objection_as_subnode_must_reference_existing_node():
    with pytest.raises(ValidationError):
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="a",
            global_objections=[
                {
                    "id": "preco",
                    "kb": "k",
                    "description": "Lead questiona o valor do investimento sempre",
                    "as_subnode": "nonexistent",
                }
            ],
            nodes=[
                NodeSpec(
                    id="a",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                ),
            ],
        )


def test_back_to_origin_accepted_as_transition_target():
    """BACK_TO_ORIGIN is a valid transition target (resolved at runtime)."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="a",
        nodes=[
            NodeSpec(
                id="a",
                prompt="x",
                exit_condition={"type": "all_fields_filled"},
                next_nodes=[{"condition": "true", "target": "obj_node"}],
                handles_objections=[
                    {
                        "id": "preco",
                        "kb": "k",
                        "description": "Lead questiona o valor do investimento sempre",
                        "as_subnode": "obj_node",
                    }
                ],
            ),
            NodeSpec(
                id="obj_node",
                prompt="x",
                exit_condition={"type": "all_fields_filled"},
                next_nodes=[{"condition": "true", "target": "BACK_TO_ORIGIN"}],
            ),
        ],
    )
    assert tf.nodes[1].next_nodes[0].target == "BACK_TO_ORIGIN"


def test_objection_ids_unique_per_scope():
    """Global and node-local can collide (node-local wins); within a scope they cannot."""
    with pytest.raises(ValidationError):
        NodeSpec(
            id="a",
            prompt="x",
            exit_condition={"type": "all_fields_filled"},
            next_nodes=[{"condition": "true", "target": "END"}],
            handles_objections=[
                {"id": "preco", "kb": "k1", "description": "first dup description here"},
                {"id": "preco", "kb": "k2", "description": "second dup description here"},
            ],
        )

    with pytest.raises(ValidationError):
        TreeFlow(
            id="tf",
            version="1.0.0",
            display_name="x",
            entry_node="a",
            global_objections=[
                {"id": "preco", "kb": "k1", "description": "first dup description here"},
                {"id": "preco", "kb": "k2", "description": "second dup description here"},
            ],
            nodes=[
                NodeSpec(
                    id="a",
                    prompt="x",
                    exit_condition={"type": "all_fields_filled"},
                    next_nodes=[{"condition": "true", "target": "END"}],
                ),
            ],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_treeflow_yaml_schema.py -v -k "subnode or back_to_origin or unique"
```

Expected: FAIL — validator doesn't check `as_subnode` references, rejects `BACK_TO_ORIGIN`, doesn't dedupe.

- [ ] **Step 3: Edit `src/ai_sdr/schemas/treeflow_yaml.py`**

Add `BACK_TO_ORIGIN_SENTINEL = "BACK_TO_ORIGIN"` constant next to `END_SENTINEL`:

```python
END_SENTINEL = "END"
BACK_TO_ORIGIN_SENTINEL = "BACK_TO_ORIGIN"
```

Add a `@model_validator(mode="after")` on `NodeSpec` that rejects duplicate objection ids:

```python
    @model_validator(mode="after")
    def _validate_objection_ids_unique(self) -> NodeSpec:
        ids = [o.id for o in self.handles_objections]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(
                f"node {self.id!r} has duplicate handles_objections ids: {sorted(dupes)}"
            )
        return self
```

In `TreeFlow._validate_graph_consistency`, after the existing transition-target check, add:

```python
        # uniqueness of global_objections ids
        global_ids = [o.id for o in self.global_objections]
        global_dupes = {x for x in global_ids if global_ids.count(x) > 1}
        if global_dupes:
            raise ValueError(
                f"duplicate global_objections ids: {sorted(global_dupes)}"
            )

        # as_subnode references must point at existing nodes
        all_objections: list[tuple[str, str | None]] = [
            (o.id, o.as_subnode) for o in self.global_objections
        ]
        for node in self.nodes:
            for o in node.handles_objections:
                all_objections.append((o.id, o.as_subnode))
        for obj_id, subnode in all_objections:
            if subnode is not None and subnode not in ids:
                raise ValueError(
                    f"objection {obj_id!r} as_subnode={subnode!r} is not declared "
                    f"in nodes (declared: {ids})"
                )
```

Update the existing transition-target loop to accept `BACK_TO_ORIGIN_SENTINEL`:

```python
        valid_targets = set(ids) | {END_SENTINEL, BACK_TO_ORIGIN_SENTINEL}
```

(Replaces the line that defines `valid_targets`.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_treeflow_yaml_schema.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/treeflow_yaml.py tests/unit/test_treeflow_yaml_schema.py
git commit -m "feat(plan4a t2): graph validator accepts BACK_TO_ORIGIN + checks as_subnode + dedupes objection ids

Plan 4a Task 2"
```

---

## Task 3: Tenant schema — `ObjectionsConfig`

**Files:**
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Modify: `tests/unit/test_tenant_yaml_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_tenant_yaml_schema.py`:

```python
from ai_sdr.schemas.tenant_yaml import ObjectionsConfig, TenantConfig


def test_objections_config_defaults():
    cfg = ObjectionsConfig()
    assert cfg.enabled is True
    assert cfg.min_confidence == 0.6
    assert cfg.max_handled_per_lead == 10
    assert cfg.history_window == 4


def test_objections_config_min_confidence_bounds():
    with pytest.raises(ValidationError):
        ObjectionsConfig(min_confidence=-0.1)
    with pytest.raises(ValidationError):
        ObjectionsConfig(min_confidence=1.5)


def test_objections_config_history_window_positive():
    with pytest.raises(ValidationError):
        ObjectionsConfig(history_window=0)
    with pytest.raises(ValidationError):
        ObjectionsConfig(history_window=25)


def test_objections_config_max_handled_positive():
    with pytest.raises(ValidationError):
        ObjectionsConfig(max_handled_per_lead=0)


def test_tenant_config_objections_optional():
    cfg = TenantConfig(
        id="example", display_name="Ex", timezone="America/Sao_Paulo"
    )
    assert cfg.objections is None


def test_tenant_config_objections_loaded():
    cfg = TenantConfig(
        id="example",
        display_name="Ex",
        timezone="America/Sao_Paulo",
        objections={"enabled": True, "min_confidence": 0.7},
    )
    assert cfg.objections is not None
    assert cfg.objections.min_confidence == 0.7
```

(Add `from pydantic import ValidationError` and `import pytest` at the top if absent.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_tenant_yaml_schema.py -v -k "objections"
```

Expected: FAIL — `ObjectionsConfig` not importable.

- [ ] **Step 3: Edit `src/ai_sdr/schemas/tenant_yaml.py`**

Add `ObjectionsConfig` after `GuardrailsConfig`:

```python
class ObjectionsConfig(BaseModel):
    """Tenant-level objection classifier configuration (Plan 4a, spec §4.4)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    max_handled_per_lead: int = Field(default=10, ge=1, le=100)
    history_window: int = Field(default=4, ge=1, le=20)
```

Add field on `TenantConfig`:

```python
    guardrails: GuardrailsConfig | None = None
    objections: ObjectionsConfig | None = None  # NEW
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_tenant_yaml_schema.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml_schema.py
git commit -m "feat(plan4a t3): ObjectionsConfig in tenant.yaml (enabled, min_confidence, max_handled_per_lead, history_window)

Plan 4a Task 3"
```

---

## Task 4: State — `ObjectionRecord` + new TalkFlowState fields

**Files:**
- Modify: `src/ai_sdr/treeflow/state.py`
- Create: `tests/unit/test_treeflow_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_treeflow_state.py`:

```python
"""Unit tests for TalkFlowState additions (Plan 4a)."""

from __future__ import annotations

import operator
from typing import get_type_hints

from ai_sdr.treeflow.state import ObjectionRecord, TalkFlowState


def test_objection_record_shape():
    rec: ObjectionRecord = {
        "objection_id": "preco",
        "detected_at_node": "qualificacao",
        "turn_index": 3,
        "quote": "tá muito caro",
    }
    assert rec["objection_id"] == "preco"


def test_state_has_new_fields():
    """TalkFlowState declares the Plan-4a fields."""
    hints = get_type_hints(TalkFlowState, include_extras=True)
    assert "objections_handled" in hints
    assert "_origin_node_id" in hints
    assert "_active_objection" in hints
    assert "_classifier_result" in hints


def test_objections_handled_uses_operator_add_reducer():
    """objections_handled must be Annotated[..., operator.add] so LangGraph appends."""
    hints = get_type_hints(TalkFlowState, include_extras=True)
    annotated = hints["objections_handled"]
    # __metadata__ is the tuple of Annotated extras
    assert hasattr(annotated, "__metadata__")
    assert operator.add in annotated.__metadata__
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_state.py -v
```

Expected: FAIL — `ObjectionRecord` not importable; new fields absent.

- [ ] **Step 3: Edit `src/ai_sdr/treeflow/state.py`**

Add `ObjectionRecord` after the existing `Message` TypedDict:

```python
class ObjectionRecord(TypedDict):
    """One row appended to ``state.objections_handled`` whenever the classifier
    detects an objection (inline OR sub-node mode)."""

    objection_id: str
    detected_at_node: str
    turn_index: int
    quote: str
```

Add 4 fields at the end of `TalkFlowState`:

```python
    completed: bool  # True when graph reached END

    # Plan 4a — objection classifier
    objections_handled: Annotated[list[ObjectionRecord], operator.add]
    _origin_node_id: str | None  # set when entering subnode, cleared on BACK_TO_ORIGIN
    _active_objection: dict[str, Any] | None  # intra-turn handoff classifier → inline
    _classifier_result: dict[str, Any] | None  # intra-turn, for observability/debug
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_state.py -v
```

Expected: PASS.

- [ ] **Step 5: Lint + type check**

```bash
make lint && make type
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/state.py tests/unit/test_treeflow_state.py
git commit -m "feat(plan4a t4): TalkFlowState gains ObjectionRecord + objections_handled + intra-turn fields

Plan 4a Task 4"
```

---

## Task 5: Classifier module — `ClassifierResult` + `classify()`

**Files:**
- Create: `src/ai_sdr/treeflow/classifier.py`
- Create: `tests/unit/test_treeflow_classifier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_treeflow_classifier.py`:

```python
"""Unit tests for the Haiku-backed objection classifier (Plan 4a, spec §4.4)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection
from ai_sdr.treeflow.classifier import ClassifierResult, classify


class _StubLLM:
    """Minimal stand-in for BaseChatModel — captures call and returns a canned result."""

    def __init__(self, canned: ClassifierResult | Exception):
        self._canned = canned
        self.calls: list[Any] = []

    def with_structured_output(self, schema: Any) -> "_StubLLM":  # noqa: ARG002
        return self

    async def ainvoke(self, messages: list[Any]) -> ClassifierResult:
        self.calls.append(messages)
        if isinstance(self._canned, Exception):
            raise self._canned
        return self._canned


@pytest.mark.asyncio
async def test_classify_empty_list_returns_none_without_calling_llm():
    llm = _StubLLM(ClassifierResult(objection_id="preco", confidence=0.9, quote="x"))
    result = await classify(
        llm=llm,
        objections=[],
        conversation=[HumanMessage(content="tá caro")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id is None
    assert result.confidence == 0.0
    assert llm.calls == []  # LLM never called


@pytest.mark.asyncio
async def test_classify_returns_llm_result():
    expected = ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")
    llm = _StubLLM(expected)
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    result = await classify(
        llm=llm,
        objections=[obj],
        conversation=[HumanMessage(content="tá caro")],
        previously_handled=[],
        history_window=4,
    )
    assert result == expected
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_classify_truncates_conversation_to_history_window():
    llm = _StubLLM(ClassifierResult(objection_id=None, confidence=0.0))
    conv = [
        HumanMessage(content="m1"),
        AIMessage(content="m2"),
        HumanMessage(content="m3"),
        AIMessage(content="m4"),
        HumanMessage(content="m5"),
        AIMessage(content="m6"),
    ]
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    await classify(
        llm=llm,
        objections=[obj],
        conversation=conv,
        previously_handled=[],
        history_window=3,
    )
    # The 1st call's message list ends with the last 3 conversation messages
    # (after the system message)
    sent_messages = llm.calls[0]
    human_or_ai = [m for m in sent_messages if isinstance(m, HumanMessage | AIMessage)]
    assert len(human_or_ai) == 3
    assert human_or_ai[-1].content == "m6"


@pytest.mark.asyncio
async def test_classify_propagates_llm_exception():
    llm = _StubLLM(RuntimeError("rate limit"))
    obj = NodeObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        await classify(
            llm=llm,
            objections=[obj],
            conversation=[HumanMessage(content="tá caro")],
            previously_handled=[],
            history_window=4,
        )


@pytest.mark.asyncio
async def test_classify_includes_previously_handled_in_context():
    """When previously_handled is non-empty, the prompt mentions those ids."""
    llm = _StubLLM(ClassifierResult(objection_id=None, confidence=0.0))
    obj = GlobalObjection(
        id="preco",
        kb="k",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )
    await classify(
        llm=llm,
        objections=[obj],
        conversation=[HumanMessage(content="oi")],
        previously_handled=["preco", "falta_tempo"],
        history_window=4,
    )
    system_text = llm.calls[0][0].content
    assert isinstance(system_text, str)
    assert "preco" in system_text
    assert "falta_tempo" in system_text


def test_classifier_result_validates():
    with pytest.raises(Exception):
        ClassifierResult(objection_id="x", confidence=1.5)  # > 1
    with pytest.raises(Exception):
        ClassifierResult(objection_id="x", confidence=-0.1)
    ok = ClassifierResult(objection_id=None, confidence=0.0)
    assert ok.quote == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_classifier.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `src/ai_sdr/treeflow/classifier.py`**

```python
"""Objection classifier — runs Haiku to detect whether the lead's latest
message raised one of the declared objections (Plan 4a, spec §4.4)."""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection


class ClassifierResult(BaseModel):
    """Structured output the classifier LLM returns."""

    objection_id: str | None = None  # None = no objection detected
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = ""  # the portion of the lead message that triggered the match


_SYSTEM_TEMPLATE = """Você é um classificador de objeções de vendas em PT-BR.

Sua tarefa: ler a conversa abaixo e identificar se a última mensagem do lead
levantou alguma das objeções listadas. Retorne `objection_id` igual ao id da
objeção detectada, ou `null` se nenhuma se aplica.

Objeções permitidas:
{objections_block}

{previously_handled_block}

Regras:
- Retorne SEMPRE um `objection_id` exatamente igual a um dos ids acima, ou `null`.
- `confidence` em [0,1] — quão certo você está. Use < 0.6 se houver dúvida real.
- `quote` é o trecho exato da última mensagem do lead que disparou a detecção.
- Se o lead apenas mencionou tema relacionado sem objeção real, retorne null.
- Se a mensagem do lead estiver vazia / só emoji / saudação, retorne null.
"""


def _format_objections(objections: list[NodeObjection | GlobalObjection]) -> str:
    lines = []
    for o in objections:
        lines.append(f"- id: {o.id}\n  description: {o.description}")
    return "\n".join(lines)


def _format_previously_handled(ids: list[str]) -> str:
    if not ids:
        return ""
    return (
        "Objeções já tratadas nesta conversa (sinalize de novo SÓ se o lead "
        "estiver claramente insistindo): " + ", ".join(ids)
    )


async def classify(
    *,
    llm: BaseChatModel,
    objections: list[NodeObjection | GlobalObjection],
    conversation: list[BaseMessage],
    previously_handled: list[str],
    history_window: int,
) -> ClassifierResult:
    """Single LLM call. Returns ClassifierResult(objection_id=None) if list empty.

    Raises whatever the LLM raises — callers are expected to catch and degrade.
    """
    if not objections:
        return ClassifierResult(objection_id=None, confidence=0.0)

    system_text = _SYSTEM_TEMPLATE.format(
        objections_block=_format_objections(objections),
        previously_handled_block=_format_previously_handled(previously_handled),
    )

    history = [m for m in conversation if isinstance(m, HumanMessage | AIMessage)]
    history = history[-history_window:]

    messages: list[BaseMessage] = [SystemMessage(content=system_text), *history]
    structured = llm.with_structured_output(ClassifierResult)
    return await structured.ainvoke(messages)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_classifier.py -v
```

Expected: all PASS.

- [ ] **Step 5: Lint + format + type**

```bash
make lint && make format && make type
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/classifier.py tests/unit/test_treeflow_classifier.py
git commit -m "feat(plan4a t5): classifier module — ClassifierResult + classify() with history_window + previously_handled

Plan 4a Task 5"
```

---

## Task 6: Objection response builder — `build_inline_objection_messages`

**Files:**
- Create: `src/ai_sdr/treeflow/objection_response.py`
- Create: `tests/unit/test_treeflow_objection_response.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_treeflow_objection_response.py`:

```python
"""Unit tests for build_inline_objection_messages (Plan 4a, spec §4.4)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ai_sdr.schemas.treeflow_yaml import (
    ExitCondition,
    NodeObjection,
    NodeSpec,
    Transition,
)
from ai_sdr.treeflow.objection_response import build_inline_objection_messages


def _make_node() -> NodeSpec:
    return NodeSpec(
        id="qualif",
        prompt="Você é uma SDR amigável em PT-BR. Pergunte sobre faturamento.",
        exit_condition=ExitCondition(type="all_fields_filled"),
        next_nodes=[Transition(condition="true", target="END")],
    )


def _make_obj() -> NodeObjection:
    return NodeObjection(
        id="preco",
        kb="kb_obj_preco",
        description="Lead questiona o valor do investimento ou compara com alternativas",
    )


def test_anthropic_with_cache_emits_three_blocks():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="conteúdo do KB aqui",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,
        provider="anthropic",
    )
    assert len(msgs) == 2  # SystemMessage + HumanMessage
    sm = msgs[0]
    assert isinstance(sm, SystemMessage)
    # content should be a list (Anthropic format)
    assert isinstance(sm.content, list)
    assert len(sm.content) == 3  # persona + objection prefix + KB
    assert sm.content[0]["text"] == node.prompt
    assert sm.content[0].get("cache_control") == {"type": "ephemeral"}
    assert "preco" in sm.content[1]["text"]
    assert obj.description in sm.content[1]["text"]
    assert sm.content[1].get("cache_control") == {"type": "ephemeral"}
    assert "conteúdo do KB aqui" in sm.content[2]["text"]
    # Block 3 (KB) is dynamic — NOT cached
    assert "cache_control" not in sm.content[2]


def test_non_anthropic_concatenates_to_single_string():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="KB content",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,  # ignored for non-anthropic
        provider="openai",
    )
    sm = msgs[0]
    assert isinstance(sm, SystemMessage)
    assert isinstance(sm.content, str)
    assert node.prompt in sm.content
    assert "preco" in sm.content
    assert "KB content" in sm.content


def test_empty_kb_appends_defensive_instruction():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="",
        conversation=[HumanMessage(content="tá caro")],
        cache_enabled=True,
        provider="anthropic",
    )
    sm = msgs[0]
    # Block 3 is the defensive instruction (no KB content)
    block3_text = sm.content[2]["text"]
    assert "peça mais detalhes" in block3_text or "informações suficientes" in block3_text


def test_conversation_appended_after_system():
    node = _make_node()
    obj = _make_obj()
    msgs = build_inline_objection_messages(
        node=node,
        objection=obj,
        kb_content="x",
        conversation=[HumanMessage(content="oi"), HumanMessage(content="tá caro")],
        cache_enabled=False,
        provider="openai",
    )
    assert len(msgs) == 3  # 1 system + 2 history
    assert msgs[1].content == "oi"
    assert msgs[2].content == "tá caro"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_objection_response.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `src/ai_sdr/treeflow/objection_response.py`**

```python
"""Build the message list for an inline objection response (Plan 4a, spec §4.4).

Inherits persona from the active Node, appends an objection-specific instruction
block, and ends with the KB-content block (or a defensive instruction when KB
is empty). Cache control follows the same pattern as build_system_messages."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection, NodeSpec

_OBJECTION_PREFIX = (
    "O lead levantou uma objeção identificada como '{id}' ({description}). "
    "Use o conhecimento abaixo. Não tente avançar a conversa nem coletar campos — "
    "apenas resolva a preocupação e convide a continuar."
)

_KB_EMPTY_FALLBACK = (
    "Não temos informações suficientes no momento sobre esta objeção. "
    "Peça mais detalhes ao lead em vez de inventar números ou afirmações."
)


def _objection_text(objection: NodeObjection | GlobalObjection) -> str:
    return _OBJECTION_PREFIX.format(id=objection.id, description=objection.description)


def _kb_block(kb_content: str) -> str:
    if kb_content.strip():
        return f"<knowledge_base>\n{kb_content}\n</knowledge_base>"
    return _KB_EMPTY_FALLBACK


def build_inline_objection_messages(
    *,
    node: NodeSpec,
    objection: NodeObjection | GlobalObjection,
    kb_content: str,
    conversation: list[BaseMessage],
    cache_enabled: bool,
    provider: str,
) -> list[BaseMessage]:
    """SystemMessage (persona + objection prefix + KB) + the conversation."""
    persona = node.prompt
    objection_prefix = _objection_text(objection)
    kb_text = _kb_block(kb_content)

    if provider == "anthropic":
        block1: dict[str, Any] = {"type": "text", "text": persona}
        block2: dict[str, Any] = {"type": "text", "text": objection_prefix}
        if cache_enabled:
            block1["cache_control"] = {"type": "ephemeral"}
            block2["cache_control"] = {"type": "ephemeral"}
        block3: dict[str, Any] = {"type": "text", "text": kb_text}
        system = SystemMessage(content=[block1, block2, block3])
    else:
        system = SystemMessage(content="\n\n".join([persona, objection_prefix, kb_text]))

    return [system, *conversation]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_objection_response.py -v
```

Expected: all PASS.

- [ ] **Step 5: Lint + format + type**

```bash
make lint && make format && make type
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/objection_response.py tests/unit/test_treeflow_objection_response.py
git commit -m "feat(plan4a t6): build_inline_objection_messages — persona + objection prefix + KB (cache-aware)

Plan 4a Task 6"
```

---

## Task 7: Compiler — entry-point routing to `:classifier`

The compiler currently registers each TreeFlow node as `sg.add_node(n.id, ...)` and `_start_router` returns the node id. After Plan 4a, every TreeFlow node ALSO gets a synthetic `:classifier` and (when applicable) `:inline` registered as separate LangGraph nodes. `_start_router` routes to `f"{current_node}:classifier"`.

This task does the minimum to make this work: register a passthrough classifier that always forwards to the main node. The actual classifier logic comes in T8. The point of splitting here is to keep the diff small and let existing tests still pass.

**Files:**
- Modify: `src/ai_sdr/treeflow/compiler.py`
- Modify: `tests/unit/test_treeflow_compiler.py` — add one test verifying the routing patch

- [ ] **Step 1: Read existing `tests/unit/test_treeflow_compiler.py`** to understand the patterns used (stub LLMs, helper builders, etc.) — preserve them.

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v
```

Confirm all existing tests pass. We don't want to break Plan 2/3 tests.

- [ ] **Step 2: Write the new failing test**

Add to `tests/unit/test_treeflow_compiler.py`:

```python
def test_start_router_routes_to_classifier_synthetic_node():
    """After Plan 4a, current_node='qualif' must route to 'qualif:classifier'
    in the compiled graph, not directly to 'qualif'."""
    # Build a minimal TreeFlow with no objections — classifier is passthrough.
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="a",
        nodes=[
            NodeSpec(
                id="a",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )
    tenant_llm = LLMDefaults(default=_dummy_llm_config())  # use existing helper
    graph = compile_treeflow(tf, tenant_llm, secrets={"anthropic_key": "x"})
    # The compiled graph should know about the synthetic node.
    # langgraph exposes nodes via .nodes (dict-like)
    node_names = set(graph.get_graph().nodes.keys())
    assert "a:classifier" in node_names
    assert "a" in node_names  # main node still exists
```

(The exact helper names — `_dummy_llm_config`, etc. — come from the existing test file. Use whatever pattern is already in place there.)

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py::test_start_router_routes_to_classifier_synthetic_node -v
```

Expected: FAIL — `a:classifier` not in graph.

- [ ] **Step 4: Edit `src/ai_sdr/treeflow/compiler.py`** — minimal patch

Add a constant near the top of the file:

```python
CLASSIFIER_SUFFIX = ":classifier"
INLINE_SUFFIX = ":inline"
```

Modify `compile_treeflow` (near the existing `sg.add_node(n.id, _make_node_fn(n))` block) to also register a passthrough classifier per node:

```python
    from langgraph.types import Command  # add to imports at top

    def _make_passthrough_classifier(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def classifier_fn(state: TalkFlowState) -> Command:  # noqa: ARG001
            return Command(goto=node.id)
        return classifier_fn

    sg: StateGraph[Any, Any, Any, Any] = StateGraph(TalkFlowState)
    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]
        sg.add_node(n.id + CLASSIFIER_SUFFIX, _make_passthrough_classifier(n))  # type: ignore[call-overload]
```

Update `_start_router` to route to the classifier suffix:

```python
    def _start_router(state: TalkFlowState) -> str:
        nid = state.get("current_node") or tf.entry_node
        if nid == "END":
            return END
        if nid not in by_id:
            raise ValueError(f"state.current_node={nid!r} not in TreeFlow")
        return nid + CLASSIFIER_SUFFIX
```

Update the conditional edges mapping:

```python
    sg.add_conditional_edges(
        START,
        _start_router,
        {**{n.id + CLASSIFIER_SUFFIX: n.id + CLASSIFIER_SUFFIX for n in tf.nodes}, END: END},
    )
```

(The `_start_router` returns the goto node id; the mapping is the set of possible return values.)

The existing `sg.add_edge(n.id, END)` stays — `N_main` still terminates the turn at END.

- [ ] **Step 5: Run all compiler tests**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v
```

Expected: PASS. The passthrough classifier preserves Plan 2/3 behavior because the `Command(goto=node.id)` causes LangGraph to traverse the existing edge `node.id → END` after one extra hop.

- [ ] **Step 6: Run existing integration test as smoke**

```bash
uv run pytest tests/integration/test_compiler_with_kb_and_guardrails.py tests/integration/test_talkflow_runtime.py -v
```

Expected: PASS. (If these need `make up`, run `make up` first.)

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py tests/unit/test_treeflow_compiler.py
git commit -m "feat(plan4a t7): compiler entry-routing patch — current_node → :classifier (passthrough for now)

Plan 4a Task 7"
```

---

## Task 8: Compiler — emit `:classifier` synthetic node with skip + classify + dispatch

Replace the passthrough with real logic: merge `global_objections + node.handles_objections`, skip when empty or `tenant.objections.enabled=false`, call `classify()`, apply confidence threshold, dispatch to `N:inline` (inline mode) or to `{as_subnode}:classifier` (sub-node mode) with the proper state updates.

**Files:**
- Modify: `src/ai_sdr/treeflow/compiler.py`
- Modify: `tests/unit/test_treeflow_compiler.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_treeflow_compiler.py`:

```python
import pytest
from unittest.mock import AsyncMock

from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.schemas.treeflow_yaml import GlobalObjection, NodeObjection
from ai_sdr.treeflow.classifier import ClassifierResult


def _tf_with_objection() -> TreeFlow:
    return TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco",
                kb="kb_obj_preco",
                description="Lead questiona o valor do investimento sempre nessa conversa",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
                handles_objections=[
                    NodeObjection(
                        id="local_x",
                        kb="kb_local",
                        description="Objeção local que aparece só neste node, descrita bem",
                    )
                ],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_classifier_skips_when_no_objections_and_no_globals():
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="a",
        nodes=[
            NodeSpec(
                id="a",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )
    # The classifier_factory is called per turn; we should NOT receive a call
    classifier_calls: list[Any] = []

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        classifier_calls.append(kwargs)
        return ClassifierResult(objection_id=None, confidence=0.0)

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
    )
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "a",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    # Run only the classifier node (simulate one step)
    # Use graph.ainvoke for the full turn — passthrough should reach main, which
    # invokes the real LLM. To isolate the classifier, we use a stub main too.
    # Simplest: invoke the graph and check that classify_fn was not called.
    await graph.ainvoke(state)
    assert classifier_calls == []  # never called when objections list is empty


@pytest.mark.asyncio
async def test_classifier_skips_when_disabled_in_tenant_config():
    tf = _tf_with_objection()
    classifier_calls: list[Any] = []

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        classifier_calls.append(kwargs)
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="x")

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=False),  # kill switch
        classify_fn=fake_classify,
    )
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    await graph.ainvoke(state)
    assert classifier_calls == []


@pytest.mark.asyncio
async def test_classifier_dispatches_to_inline_on_detection_above_threshold():
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.8, quote="tá caro")

    # We don't have N:inline implemented yet — until T9, the test just verifies
    # the classifier wrote _active_objection to the state delta. We'll use a
    # capture sink for state mutations.
    # Simpler: directly invoke the classifier node function via the graph
    # builder helper. To keep this self-contained, we re-build the graph and
    # inspect the resulting state after one step.

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True, min_confidence=0.6),
        classify_fn=fake_classify,
    )
    # We expect the test to FAIL at this point because N:inline doesn't exist.
    # That's OK — we re-enable it in T9.
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    with pytest.raises(Exception):
        # Inline node not registered yet
        await graph.ainvoke(state)


@pytest.mark.asyncio
async def test_classifier_below_threshold_goes_to_main():
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.4, quote="tá caro")

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True, min_confidence=0.6),
        classify_fn=fake_classify,
    )
    # below threshold → goes to N_main, which calls the LLM.
    # Use a stub main LLM factory that returns a canned response.
    # (Use the same pattern as existing compiler tests for the main LLM.)
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    final_state = await graph.ainvoke(state)
    # objections_handled should be empty (deflect didn't fire)
    assert final_state.get("objections_handled", []) == []


@pytest.mark.asyncio
async def test_classifier_exception_falls_through_to_main():
    tf = _tf_with_objection()

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        raise RuntimeError("haiku rate limit")

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
    )
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    # Should NOT propagate — should go to N_main
    final_state = await graph.ainvoke(state)
    assert final_state.get("objections_handled", []) == []
```

You'll need a `_tenant_llm_with_classifier()` helper. Reuse / extend the existing `_dummy_llm_config()` pattern; it should return:

```python
def _tenant_llm_with_classifier() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        ),
        classifier=LLMConfig(
            provider="anthropic",
            model="claude-haiku-4-5",
            api_key_ref="secrets/anthropic_key",
        ),
    )
```

Also a stub main LLM factory so the test path with main doesn't try to call real LLM. Adapt from the existing test file's pattern (or extend the existing `_stub_llm_factory`).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v -k "classifier"
```

Expected: FAIL.

- [ ] **Step 3: Edit `src/ai_sdr/treeflow/compiler.py`** — real classifier logic

Add an import:

```python
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
import structlog

from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.treeflow.classifier import ClassifierResult, classify as default_classify

logger = structlog.get_logger(__name__)
```

Add a type alias near the existing factory aliases:

```python
ClassifyFn = Callable[..., Awaitable[ClassifierResult]]
"""Test seam — production passes ai_sdr.treeflow.classifier.classify."""
```

Extend `compile_treeflow` signature:

```python
def compile_treeflow(
    tf: TreeFlow,
    tenant_llm: LLMDefaults,
    secrets: dict[str, str],
    *,
    guardrails: GuardrailsConfig | None = None,
    objections: ObjectionsConfig | None = None,
    tenant_id: uuid.UUID | None = None,
    llm_factory: LLMFactory | None = None,
    embedder_factory: EmbedderFactory | None = None,
    kb_session_factory: KbSessionFactory | None = None,
    classify_fn: ClassifyFn | None = None,
    checkpointer: Any = None,
) -> Any:
```

Inside `compile_treeflow`, after the existing setup, add a helper that merges globals + node-local (node-local wins on id collision):

```python
    def _applicable_objections(
        node: NodeSpec,
    ) -> list[NodeObjection | GlobalObjection]:
        merged: dict[str, NodeObjection | GlobalObjection] = {}
        for g in tf.global_objections:
            merged[g.id] = g
        for o in node.handles_objections:
            merged[o.id] = o  # node-local wins
        return list(merged.values())
```

Now replace the passthrough with the real classifier factory. The classifier needs access to the tenant's `classifier` LLM config. If `tenant_llm.classifier` is None, fall back to `tenant_llm.default`.

```python
    classify_impl: ClassifyFn = classify_fn or default_classify
    objections_cfg = objections or ObjectionsConfig()  # defaults preserve enabled=true

    def _make_classifier(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        applicable = _applicable_objections(node)

        async def classifier_fn(state: TalkFlowState) -> Command:
            tenant_id_for_log = str(tenant_id) if tenant_id is not None else None
            # Skip if no objections in scope OR kill switch
            if not applicable or not objections_cfg.enabled:
                logger.info(
                    "objection.classifier.skipped",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    reason="no_objections" if not applicable else "disabled",
                )
                return Command(goto=node.id)

            # Build the conversation (messages + last user input as HumanMessage)
            conversation: list[Any] = []
            for m in state.get("messages", []):
                if m["role"] == "user":
                    conversation.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    conversation.append(AIMessage(content=m["content"]))
            user_input = state.get("last_user_input", "")
            if user_input:
                conversation.append(HumanMessage(content=user_input))

            # Build the classifier LLM
            classifier_cfg = tenant_llm.classifier or tenant_llm.default
            classifier_llm = llm_fn(classifier_cfg, secrets, node.id)

            previously_handled = [
                r["objection_id"] for r in state.get("objections_handled", []) or []
            ]

            try:
                result = await classify_impl(
                    llm=classifier_llm,
                    objections=applicable,
                    conversation=conversation,
                    previously_handled=previously_handled,
                    history_window=objections_cfg.history_window,
                )
            except Exception as exc:
                logger.warning(
                    "objection.classifier.error",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                return Command(goto=node.id)

            allowed_ids = {o.id for o in applicable}
            if result.objection_id is not None and result.objection_id not in allowed_ids:
                logger.warning(
                    "objection.classifier.hallucinated_id",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    returned_id=result.objection_id,
                    allowed_ids=sorted(allowed_ids),
                )
                return Command(goto=node.id)

            if result.objection_id is None or result.confidence < objections_cfg.min_confidence:
                logger.info(
                    "objection.classifier.no_match",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    max_confidence_seen=result.confidence,
                )
                return Command(goto=node.id)

            # Detection above threshold
            detected = next(o for o in applicable if o.id == result.objection_id)
            scope = "local" if any(o.id == detected.id for o in node.handles_objections) else "global"
            logger.info(
                "objection.classifier.detected",
                tenant_id=tenant_id_for_log,
                node_id=node.id,
                objection_id=detected.id,
                confidence=result.confidence,
                quote=result.quote,
                scope=scope,
            )

            handled_count = len(state.get("objections_handled", []) or []) + 1
            if handled_count > objections_cfg.max_handled_per_lead:
                logger.warning(
                    "objection.threshold.exceeded",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    count=handled_count,
                    threshold=objections_cfg.max_handled_per_lead,
                )

            if detected.as_subnode is None:
                # Inline mode — hand off to N:inline (T9 will implement the node)
                return Command(
                    goto=node.id + INLINE_SUFFIX,
                    update={
                        "_active_objection": detected.model_dump(),
                        "_classifier_result": result.model_dump(),
                    },
                )
            else:
                # Sub-node mode — append record now (we know we're entering the subnode),
                # set _origin_node_id, route to subnode's classifier.
                record: ObjectionRecord = {  # type: ignore[name-defined]
                    "objection_id": detected.id,
                    "detected_at_node": node.id,
                    "turn_index": len(state.get("messages", []) or []) // 2,
                    "quote": result.quote,
                }
                logger.info(
                    "objection.subnode.entered",
                    tenant_id=tenant_id_for_log,
                    node_id=node.id,
                    objection_id=detected.id,
                    subnode_id=detected.as_subnode,
                    origin_node_id=node.id,
                )
                return Command(
                    goto=detected.as_subnode + CLASSIFIER_SUFFIX,
                    update={
                        "_origin_node_id": node.id,
                        "objections_handled": [record],
                    },
                )

        return classifier_fn
```

Add `from ai_sdr.treeflow.state import ObjectionRecord` to imports (and remove the `# type: ignore[name-defined]` once the import is in place).

Replace the existing registration loop:

```python
    sg: StateGraph[Any, Any, Any, Any] = StateGraph(TalkFlowState)
    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]
        sg.add_node(n.id + CLASSIFIER_SUFFIX, _make_classifier(n))  # type: ignore[call-overload]
```

(The START conditional edges already point at `:classifier` after T7. `Command(goto=X)` returned by the classifier node is resolved by LangGraph directly to any registered node — no edges-map update required here.)

- [ ] **Step 4: Run the classifier tests**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v -k "classifier"
```

Expected: PASS for skip + threshold + exception tests. The `test_classifier_dispatches_to_inline_on_detection_above_threshold` test should still FAIL (with a "node N:inline not found" error) — that's intentional, T9 fixes it.

- [ ] **Step 5: Lint + type**

```bash
make lint && make type
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py tests/unit/test_treeflow_compiler.py
git commit -m "feat(plan4a t8): compiler emits real :classifier node — merge globals, threshold, dispatch, fail-safe to main

Plan 4a Task 8"
```

---

## Task 9: Compiler — emit `:inline` synthetic node

When the classifier dispatches to `N:inline`, run the inline-response LLM call (persona of N + objection prefix + KB, wrapped in `run_with_guardrails`), append `ObjectionRecord`, and end the turn at END.

**Files:**
- Modify: `src/ai_sdr/treeflow/compiler.py`
- Modify: `tests/unit/test_treeflow_compiler.py`

- [ ] **Step 1: Re-enable the failing test from T8**

In `tests/unit/test_treeflow_compiler.py`, replace the body of `test_classifier_dispatches_to_inline_on_detection_above_threshold` with the real expectations:

```python
@pytest.mark.asyncio
async def test_classifier_dispatches_to_inline_on_detection_above_threshold():
    tf = _tf_with_objection()
    # node "qualif" has a global_objections["preco"] (no as_subnode) → inline path

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")

    # Inline path calls build_inline_objection_messages + run_with_guardrails +
    # the main LLM (because it reuses N's prompt). Stub the main LLM factory.

    # Use a stub that returns a structured-output-like result with response_text.
    # The simplest path: pass a kb_session_factory that returns no chunks (so KB
    # block is the defensive instruction).

    captured_responses: list[str] = []

    def stub_llm_factory(cfg: Any, secrets: Any, node_id: str) -> Any:
        # Same stub used for main + classifier
        return _make_stub_llm_returning(
            response_text="ok, deixa eu explicar o valor", captured=captured_responses
        )

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True, min_confidence=0.6),
        classify_fn=fake_classify,
        llm_factory=stub_llm_factory,
    )
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    final_state = await graph.ainvoke(state)

    # objections_handled was appended
    handled = final_state.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"
    assert handled[0]["detected_at_node"] == "qualif"

    # current_node MUST stay at 'qualif' — inline doesn't advance
    assert final_state.get("current_node") == "qualif"

    # collected must be untouched
    assert final_state.get("collected", {}) == {}

    # The agent response is the inline response
    assert final_state.get("last_agent_response") == "ok, deixa eu explicar o valor"
```

Implement `_make_stub_llm_returning` in the test file (or use the existing stub pattern). Pseudocode for it:

```python
def _make_stub_llm_returning(response_text: str, captured: list[str]) -> Any:
    class _Stub:
        def with_structured_output(self, schema: Any) -> "_Stub":
            return self

        async def ainvoke(self, messages: list[Any]) -> Any:
            captured.append(response_text)
            # Return whatever the structured output expects. For the main flow,
            # build_structured_model returns a pydantic model with response_text
            # + per-collect fields + prices_mentioned + products_mentioned.
            # Build a dynamic stand-in that satisfies ExtractResultProto.
            from types import SimpleNamespace
            return SimpleNamespace(
                response_text=response_text,
                prices_mentioned=[],
                products_mentioned=[],
            )
    return _Stub()
```

(Match this to whatever structure the existing compiler tests already use. Adapt if there's already a helper for stub LLMs.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py::test_classifier_dispatches_to_inline_on_detection_above_threshold -v
```

Expected: FAIL — N:inline node not registered.

- [ ] **Step 3: Edit `src/ai_sdr/treeflow/compiler.py`** — add the inline factory

Add an import at the top:

```python
from ai_sdr.treeflow.objection_response import build_inline_objection_messages
```

Add the `_make_inline_response` factory near `_make_classifier`:

```python
    def _make_inline_response(node: NodeSpec) -> Callable[[TalkFlowState], Any]:
        async def inline_fn(state: TalkFlowState) -> dict[str, Any]:
            tenant_id_for_log = str(tenant_id) if tenant_id is not None else None
            active = state.get("_active_objection")
            classifier_result = state.get("_classifier_result")
            assert active is not None, "inline_fn entered without _active_objection — compiler bug"

            # Rehydrate objection from dump (could be NodeObjection or GlobalObjection)
            try:
                obj = NodeObjection(**active)
            except Exception:
                obj = GlobalObjection(**active)

            # Retrieve KB chunks (Plan 3 retriever)
            kb_content = ""
            if tenant_id is not None and kb_session_factory is not None and tenant_llm.embeddings is not None:
                embedder = await emb_fn(secrets, tenant_llm.embeddings)
                kb_session = await kb_session_factory()
                try:
                    chunks = await retrieve(
                        kb_session,
                        tenant_id=tenant_id,
                        kb_refs=[KBRef(id=obj.kb, top_k=3, min_score=0.0)],
                        query=state.get("last_user_input", ""),
                        embedder=embedder,
                    )
                    if not chunks:
                        logger.info(
                            "objection.kb.empty",
                            tenant_id=tenant_id_for_log,
                            node_id=node.id,
                            kb_id=obj.kb,
                        )
                    else:
                        kb_content = _render_kb_block(chunks)
                except Exception as exc:
                    logger.warning(
                        "objection.kb.missing",
                        tenant_id=tenant_id_for_log,
                        node_id=node.id,
                        kb_id=obj.kb,
                        error_message=str(exc),
                    )

            # Build messages
            llm_cfg = node.llm or tenant_llm.default
            llm = llm_fn(llm_cfg, secrets, node.id)

            history: list[Any] = []
            for m in state.get("messages", []):
                if m["role"] == "user":
                    history.append(HumanMessage(content=m["content"]))
                elif m["role"] == "assistant":
                    history.append(AIMessage(content=m["content"]))
            user_input = state.get("last_user_input", "")
            if user_input:
                history.append(HumanMessage(content=user_input))

            base_messages = build_inline_objection_messages(
                node=node,
                objection=obj,
                kb_content=kb_content,
                conversation=history,
                cache_enabled=tenant_llm.cache_enabled,
                provider=llm_cfg.provider,
            )

            model = build_structured_model(node.collects, guardrails=guardrails)

            async def _invoke_inner(msgs: list[Any]) -> Any:
                return await extract(llm, model, msgs)

            recent_history = state.get("messages", [])[-4:]
            result = await run_with_guardrails(
                inner=_invoke_inner,
                base_messages=base_messages,
                guardrails=guardrails,
                critical=node.critical,
                kb_chunks=[],  # KB already rendered into the system message above
                recent_history=recent_history,
                tenant_llm=tenant_llm,
                secrets=secrets,
                llm_factory=llm_fn,
            )

            record: ObjectionRecord = {
                "objection_id": obj.id,
                "detected_at_node": node.id,
                "turn_index": len(state.get("messages", []) or []) // 2,
                "quote": (classifier_result or {}).get("quote", ""),
            }
            logger.info(
                "objection.inline.responded",
                tenant_id=tenant_id_for_log,
                node_id=node.id,
                objection_id=obj.id,
            )

            new_msgs: list[Message] = []
            if user_input:
                new_msgs.append({"role": "user", "content": user_input})
            new_msgs.append({"role": "assistant", "content": result.response_text})

            return {
                "messages": new_msgs,
                "last_agent_response": result.response_text,
                "last_user_input": "",
                # current_node UNCHANGED — next turn re-enters N:classifier
                "objections_handled": [record],
                # clear intra-turn fields
                "_active_objection": None,
                "_classifier_result": None,
            }

        return inline_fn
```

Add `KBRef` to imports (`from ai_sdr.schemas.treeflow_yaml import KBRef, NodeObjection, NodeSpec, TreeFlow`).

Add registration in the node-registration loop:

```python
    for n in tf.nodes:
        sg.add_node(n.id, _make_node_fn(n))  # type: ignore[call-overload]
        sg.add_node(n.id + CLASSIFIER_SUFFIX, _make_classifier(n))  # type: ignore[call-overload]
        # Only register :inline when N has at least one inline-mode objection
        node_inline_objs = [o for o in n.handles_objections if o.as_subnode is None]
        has_inline_globals = any(g.as_subnode is None for g in tf.global_objections)
        if node_inline_objs or has_inline_globals:
            sg.add_node(n.id + INLINE_SUFFIX, _make_inline_response(n))  # type: ignore[call-overload]
            sg.add_edge(n.id + INLINE_SUFFIX, END)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py::test_classifier_dispatches_to_inline_on_detection_above_threshold -v
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: all PASS. (Pre-existing tests should be untouched since current_node stays the same after inline.)

- [ ] **Step 6: Lint + type**

```bash
make lint && make type
```

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py tests/unit/test_treeflow_compiler.py
git commit -m "feat(plan4a t9): compiler emits :inline node — KB retrieve + run_with_guardrails + appends ObjectionRecord

Plan 4a Task 9"
```

---

## Task 10: Compiler — `BACK_TO_ORIGIN` resolution in `_route`

When a sub-node's transition target is `BACK_TO_ORIGIN`, the node's `node_fn` (returned by `_make_node_fn`) must resolve to `state._origin_node_id` and clear `_origin_node_id`. The cleanest place is inside the existing `_route` helper.

**Files:**
- Modify: `src/ai_sdr/treeflow/compiler.py`
- Modify: `tests/unit/test_treeflow_compiler.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_treeflow_compiler.py`:

```python
@pytest.mark.asyncio
async def test_back_to_origin_resolves_via_origin_node_id():
    """A sub-node N_obj transitions BACK_TO_ORIGIN → next current_node = origin id, _origin_node_id cleared."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
                handles_objections=[
                    NodeObjection(
                        id="preco",
                        kb="kb_obj_preco",
                        description="Lead questiona o valor do investimento sempre",
                        as_subnode="obj_preco_node",
                    )
                ],
            ),
            NodeSpec(
                id="obj_preco_node",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="BACK_TO_ORIGIN")],
            ),
        ],
    )

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="tá caro")

    captured: list[str] = []

    def stub_llm_factory(cfg: Any, secrets: Any, node_id: str) -> Any:
        return _make_stub_llm_returning("subnode answer", captured)

    graph = compile_treeflow(
        tf,
        _tenant_llm_with_classifier(),
        secrets={"anthropic_key": "x"},
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=stub_llm_factory,
    )

    # Turn 1: classifier detects preco → goto obj_preco_node:classifier
    # → passthrough (obj_preco_node has no objections) → obj_preco_node main
    # → run, exit_condition true → transition BACK_TO_ORIGIN
    # → _route resolves to "qualif", clears _origin_node_id
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá caro",
    }
    final = await graph.ainvoke(state)
    assert final["current_node"] == "qualif"
    assert final.get("_origin_node_id") is None
    handled = final.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"


@pytest.mark.asyncio
async def test_back_to_origin_orphan_falls_back_to_entry_node():
    """If a transition fires BACK_TO_ORIGIN with _origin_node_id=None, fall back to entry_node + warn."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="a",
        nodes=[
            NodeSpec(
                id="a",
                prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="BACK_TO_ORIGIN")],
            ),
        ],
    )
    captured: list[str] = []

    def stub_llm_factory(cfg: Any, secrets: Any, node_id: str) -> Any:
        return _make_stub_llm_returning("ok", captured)

    graph = compile_treeflow(
        tf, _tenant_llm_with_classifier(), secrets={"anthropic_key": "x"},
        llm_factory=stub_llm_factory,
    )
    state: TalkFlowState = {  # type: ignore[typeddict-item]
        "current_node": "a",
        "collected": {},
        "messages": [],
        "last_user_input": "oi",
        # _origin_node_id intentionally absent
    }
    final = await graph.ainvoke(state)
    # entry_node is "a" → resolution fallback is "a"
    assert final["current_node"] == "a"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v -k "back_to_origin"
```

Expected: FAIL — `_route` doesn't know about `BACK_TO_ORIGIN`.

- [ ] **Step 3: Edit `_route` in `src/ai_sdr/treeflow/compiler.py`**

Make sure `BACK_TO_ORIGIN_SENTINEL` is imported alongside `END_SENTINEL`:

```python
from ai_sdr.schemas.treeflow_yaml import (
    BACK_TO_ORIGIN_SENTINEL,
    END_SENTINEL,
    KBRef,
    NodeObjection,
    NodeSpec,
    TreeFlow,
)
```

Change `_route` to accept state and resolve the sentinel:

```python
def _route(
    node: NodeSpec,
    collected: dict[str, Any],
    state: TalkFlowState,
    entry_node: str,
) -> tuple[str, bool, dict[str, Any]]:
    """Return (next_current_node, completed, extra_state_update).

    extra_state_update may carry {'_origin_node_id': None} when we resolve BACK_TO_ORIGIN.
    """
    if not _exit_satisfied(node, collected):
        return (node.id, False, {})
    for tr in node.next_nodes:
        if eval_bool(tr.condition, collected):
            target = tr.target
            if target == END_SENTINEL:
                return (END_SENTINEL, True, {})
            if target == BACK_TO_ORIGIN_SENTINEL:
                origin = state.get("_origin_node_id")
                if origin is None:
                    logger.warning(
                        "objection.subnode.orphan_return",
                        node_id=node.id,
                        fallback_target=entry_node,
                    )
                    return (entry_node, False, {})
                logger.info(
                    "objection.subnode.exited",
                    node_id=node.id,
                    subnode_id=node.id,
                    returned_to_node_id=origin,
                )
                return (origin, False, {"_origin_node_id": None})
            return (target, False, {})
    # nothing matched — stay
    return (node.id, False, {})
```

Update the caller in `_make_node_fn`:

```python
            next_node, completed, extra = _route(node, collected_after, state, tf.entry_node)

            new_msgs: list[Message] = []
            if user_input:
                new_msgs.append({"role": "user", "content": user_input})
            new_msgs.append({"role": "assistant", "content": response_text})

            update: dict[str, Any] = {
                "collected": collected_after,
                "messages": new_msgs,
                "last_agent_response": response_text,
                "last_user_input": "",
                "current_node": next_node,
                "completed": completed,
            }
            update.update(extra)
            return update
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_treeflow_compiler.py -v -k "back_to_origin"
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v
```

Expected: all PASS. (Existing tests may have called `_route(node, collected)` — if so, update them to pass empty state and entry_node, OR keep backward-compat by defaulting state/entry_node to safe values. The simplest path: search-replace existing callers in tests.)

```bash
grep -rn "_route(" tests/ src/
```

Expected: only the new caller. If any test calls `_route` directly, fix to the new signature.

- [ ] **Step 6: Lint + type**

```bash
make lint && make type
```

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/treeflow/compiler.py tests/unit/test_treeflow_compiler.py
git commit -m "feat(plan4a t10): _route resolves BACK_TO_ORIGIN sentinel via state._origin_node_id (fallback entry_node)

Plan 4a Task 10"
```

---

## Task 11: Integration test — full turn cycle with mocked classifier

**Files:**
- Create: `tests/integration/test_objection_runtime.py`

- [ ] **Step 1: Inspect existing integration test patterns**

```bash
sed -n '1,60p' tests/integration/test_talkflow_runtime.py
```

Note the fixtures: how the postgres session is set up, how the checkpointer is wired, how tenants/treeflows are loaded. Reuse them.

- [ ] **Step 2: Create the test**

Create `tests/integration/test_objection_runtime.py`:

```python
"""Integration test: full turn cycle with mocked classifier (Plan 4a).

Uses the real Postgres checkpointer; mocks the classifier LLM. Verifies that:
1. Detection above threshold deflects to :inline, appends ObjectionRecord,
   keeps current_node unchanged.
2. The next turn re-enters the classifier (because current_node stayed).
3. max_handled_per_lead emits the warning event when exceeded.
"""

from __future__ import annotations

from typing import Any

import pytest

from ai_sdr.schemas.tenant_yaml import ObjectionsConfig
from ai_sdr.schemas.treeflow_yaml import (
    ExitCondition,
    GlobalObjection,
    NodeSpec,
    Transition,
    TreeFlow,
)
from ai_sdr.treeflow.classifier import ClassifierResult


@pytest.mark.asyncio
@pytest.mark.integration
async def test_inline_objection_appends_record_and_does_not_advance(
    postgres_checkpointer,  # fixture from conftest
    tenant_llm_with_classifier,  # fixture
    secrets,  # fixture
):
    """Reuses the same fixtures as tests/integration/test_talkflow_runtime.py."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco",
                kb="kb_obj_preco",
                description="Lead questiona o valor do investimento ou compara preços com alternativas",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda algo curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.85, quote="tá caro")

    # Use the project's stub-LLM helper (extend tests/integration/conftest.py
    # if not present — see test_talkflow_runtime.py for the established pattern).
    from tests.integration.conftest import stub_llm_factory  # if exists

    from ai_sdr.treeflow.compiler import compile_treeflow

    graph = compile_treeflow(
        tf,
        tenant_llm_with_classifier,
        secrets=secrets,
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=stub_llm_factory(response_text="explico já"),
        checkpointer=postgres_checkpointer,
    )

    thread_id = "test-tenant:test-talkflow-1"
    config = {"configurable": {"thread_id": thread_id}}

    # Turn 1
    state = {
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá muito caro",
    }
    final = await graph.ainvoke(state, config=config)

    handled = final.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"
    assert final["current_node"] == "qualif"
    assert final["last_agent_response"] == "explico já"

    # Turn 2 — lead asks no-objection question; classifier returns null
    async def no_match(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id=None, confidence=0.0)

    graph2 = compile_treeflow(
        tf,
        tenant_llm_with_classifier,
        secrets=secrets,
        objections=ObjectionsConfig(enabled=True),
        classify_fn=no_match,
        llm_factory=stub_llm_factory(response_text="ok, fechado"),
        checkpointer=postgres_checkpointer,
    )
    state2 = {"last_user_input": "fechado"}
    final2 = await graph2.ainvoke(state2, config=config)
    # objections_handled grew? — operator.add appends across turns
    handled2 = final2.get("objections_handled", [])
    assert len(handled2) == 1  # no new objection this turn
    # The main node ran and advanced (exit_condition all_fields_filled = true with no required collects)
    assert final2["current_node"] == "END"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_max_handled_threshold_emits_warning(
    postgres_checkpointer,
    tenant_llm_with_classifier,
    secrets,
    caplog,
):
    """When objections_handled crosses max_handled_per_lead, a warning event fires."""
    import logging

    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco", kb="kb_obj_preco",
                description="Lead questiona o valor do investimento ou compara preços com alternativas",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda algo curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )

    async def always_preco(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    from tests.integration.conftest import stub_llm_factory
    from ai_sdr.treeflow.compiler import compile_treeflow

    graph = compile_treeflow(
        tf,
        tenant_llm_with_classifier,
        secrets=secrets,
        objections=ObjectionsConfig(enabled=True, max_handled_per_lead=2),
        classify_fn=always_preco,
        llm_factory=stub_llm_factory(response_text="respondendo"),
        checkpointer=postgres_checkpointer,
    )

    caplog.set_level(logging.WARNING)
    config = {"configurable": {"thread_id": "test-threshold:1"}}
    # 3 turns deflecting → threshold (2) crossed on turn 3
    for i in range(3):
        await graph.ainvoke(
            {"current_node": "qualif", "collected": {}, "messages": [], "last_user_input": f"caro {i}"},
            config=config,
        )

    threshold_warnings = [r for r in caplog.records if "objection.threshold.exceeded" in r.getMessage()]
    assert len(threshold_warnings) >= 1
```

If the project doesn't yet have the fixtures named above, extend `tests/integration/conftest.py`:

```python
@pytest.fixture
def tenant_llm_with_classifier() -> LLMDefaults:
    return LLMDefaults(
        default=LLMConfig(
            provider="anthropic", model="claude-sonnet-4-6",
            api_key_ref="secrets/anthropic_key",
        ),
        classifier=LLMConfig(
            provider="anthropic", model="claude-haiku-4-5",
            api_key_ref="secrets/anthropic_key",
        ),
    )


def stub_llm_factory(response_text: str):
    def factory(cfg, secrets, node_id):
        # Same pattern as the existing one in tests/unit/test_treeflow_compiler.py
        ...
    return factory
```

(If the existing test_talkflow_runtime.py uses a different fixture/factory shape, mirror that.)

- [ ] **Step 3: Run the test**

Bring up infra if needed: `make up`. Then:

```bash
uv run pytest tests/integration/test_objection_runtime.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_objection_runtime.py tests/integration/conftest.py
git commit -m "test(plan4a t11): integration test — inline objection appends record, does not advance, threshold warning

Plan 4a Task 11"
```

---

## Task 12: Integration test — sub-node mode

**Files:**
- Modify: `tests/integration/test_objection_runtime.py`

- [ ] **Step 1: Add the test**

Append to `tests/integration/test_objection_runtime.py`:

```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_subnode_mode_routes_through_subnode_and_returns_to_origin(
    postgres_checkpointer,
    tenant_llm_with_classifier,
    secrets,
):
    """as_subnode mode: classifier → subnode:classifier → subnode (main) → BACK_TO_ORIGIN."""
    tf = TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
                handles_objections=[
                    {
                        "id": "preco",
                        "kb": "kb_obj_preco",
                        "description": "Lead questiona o valor do investimento ou compara com alternativas",
                        "as_subnode": "obj_preco_node",
                    }
                ],
            ),
            NodeSpec(
                id="obj_preco_node",
                prompt="responda sobre preço",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="BACK_TO_ORIGIN")],
            ),
        ],
    )

    async def fake_classify(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="tá caro")

    from tests.integration.conftest import stub_llm_factory
    from ai_sdr.treeflow.compiler import compile_treeflow

    graph = compile_treeflow(
        tf,
        tenant_llm_with_classifier,
        secrets=secrets,
        objections=ObjectionsConfig(enabled=True),
        classify_fn=fake_classify,
        llm_factory=stub_llm_factory(response_text="subnode response"),
        checkpointer=postgres_checkpointer,
    )

    config = {"configurable": {"thread_id": "test:subnode-1"}}
    state = {
        "current_node": "qualif",
        "collected": {},
        "messages": [],
        "last_user_input": "tá muito caro",
    }
    final = await graph.ainvoke(state, config=config)

    # current_node returned to origin via BACK_TO_ORIGIN
    assert final["current_node"] == "qualif"
    assert final.get("_origin_node_id") is None  # cleared

    # ObjectionRecord appended by classifier when dispatching to subnode
    handled = final.get("objections_handled", [])
    assert len(handled) == 1
    assert handled[0]["objection_id"] == "preco"
    assert handled[0]["detected_at_node"] == "qualif"

    # The agent response is the subnode's response
    assert final["last_agent_response"] == "subnode response"
```

- [ ] **Step 2: Run it**

```bash
uv run pytest tests/integration/test_objection_runtime.py -v -k "subnode"
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_objection_runtime.py
git commit -m "test(plan4a t12): integration test — as_subnode mode (subnode entered + BACK_TO_ORIGIN)

Plan 4a Task 12"
```

---

## Task 13: Integration test — cross-tenant isolation + version upgrade

**Files:**
- Create: `tests/integration/test_objection_isolation.py`

- [ ] **Step 1: Inspect the existing `test_rls_isolation.py`**

```bash
sed -n '1,60p' tests/integration/test_rls_isolation.py
```

Use the same fixture pattern (`set_tenant_context`, two tenants, etc.).

- [ ] **Step 2: Create the test**

```python
"""Cross-tenant isolation + TreeFlow version-upgrade tests for Plan 4a."""

from __future__ import annotations

from typing import Any

import pytest

# ... imports analogous to test_objection_runtime.py ...


def _single_node_tf_with_preco() -> TreeFlow:
    return TreeFlow(
        id="tf",
        version="1.0.0",
        display_name="x",
        entry_node="qualif",
        global_objections=[
            GlobalObjection(
                id="preco", kb="kb_obj_preco",
                description="Lead questiona o valor do investimento ou compara preços com alternativas",
            )
        ],
        nodes=[
            NodeSpec(
                id="qualif",
                prompt="responda algo curto",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_objections_handled_isolated_between_threads(
    postgres_checkpointer, tenant_llm_with_classifier, secrets,
):
    """Different thread_ids do not share objections_handled (the checkpointer
    keys by thread_id, which by convention is f'{tenant_id}:{talkflow_id}')."""
    from tests.integration.conftest import stub_llm_factory
    from ai_sdr.treeflow.compiler import compile_treeflow

    tf = _single_node_tf_with_preco()

    async def deflect(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    async def no_match(**kwargs: Any) -> ClassifierResult:
        return ClassifierResult(objection_id=None, confidence=0.0)

    graph_a = compile_treeflow(
        tf, tenant_llm_with_classifier, secrets=secrets,
        objections=ObjectionsConfig(enabled=True),
        classify_fn=deflect,
        llm_factory=stub_llm_factory(response_text="A"),
        checkpointer=postgres_checkpointer,
    )
    graph_b = compile_treeflow(
        tf, tenant_llm_with_classifier, secrets=secrets,
        objections=ObjectionsConfig(enabled=True),
        classify_fn=no_match,
        llm_factory=stub_llm_factory(response_text="B"),
        checkpointer=postgres_checkpointer,
    )
    config_a = {"configurable": {"thread_id": "tenant-a:tf-1"}}
    config_b = {"configurable": {"thread_id": "tenant-b:tf-1"}}

    state_a = {"current_node": "qualif", "collected": {}, "messages": [], "last_user_input": "caro"}
    state_b = {"current_node": "qualif", "collected": {}, "messages": [], "last_user_input": "ok"}

    final_a = await graph_a.ainvoke(state_a, config=config_a)
    final_b = await graph_b.ainvoke(state_b, config=config_b)

    assert len(final_a.get("objections_handled", [])) == 1
    assert len(final_b.get("objections_handled", [])) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_v1_treeflow_without_objections_never_calls_classifier(
    postgres_checkpointer, tenant_llm_with_classifier, secrets,
):
    """A TreeFlow without any objections (v1-style) never invokes the classifier
    even when tenant.objections.enabled=true. Plan 4a invariant: zero cost
    for TreeFlows that haven't opted in to objections."""
    from tests.integration.conftest import stub_llm_factory
    from ai_sdr.treeflow.compiler import compile_treeflow

    tf_v1 = TreeFlow(
        id="tf", version="1.0.0", display_name="x", entry_node="qualif",
        nodes=[
            NodeSpec(
                id="qualif", prompt="x",
                exit_condition=ExitCondition(type="all_fields_filled"),
                next_nodes=[Transition(condition="true", target="END")],
            ),
        ],
    )

    classify_calls: list[Any] = []

    async def tracked_classify(**kwargs: Any) -> ClassifierResult:
        classify_calls.append(kwargs)
        return ClassifierResult(objection_id="preco", confidence=0.9, quote="caro")

    graph = compile_treeflow(
        tf_v1, tenant_llm_with_classifier, secrets=secrets,
        objections=ObjectionsConfig(enabled=True),  # tenant has it on …
        classify_fn=tracked_classify,
        llm_factory=stub_llm_factory(response_text="ok"),
        checkpointer=postgres_checkpointer,
    )
    config = {"configurable": {"thread_id": "v1-no-obj:1"}}

    await graph.ainvoke(
        {"current_node": "qualif", "collected": {}, "messages": [], "last_user_input": "tá caro"},
        config=config,
    )
    assert classify_calls == []  # … but TreeFlow has no objections → never called
```

These two tests document the invariants. Wire them up using the same fixture patterns as T11. If a fixture doesn't exist yet, mirror it from `test_rls_isolation.py` and `test_talkflow_runtime.py`.

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/integration/test_objection_isolation.py -v
```

Expected: PASS (after replacing `pytest.fail` with real assertions).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_objection_isolation.py
git commit -m "test(plan4a t13): integration tests — cross-tenant isolation + version upgrade safety

Plan 4a Task 13"
```

---

## Task 14: Example tenant scaffolding (YAMLs + KB md files)

**Files:**
- Modify: `tenants/example/tenant.yaml`
- Modify: `tenants/example/treeflows/example.yaml`
- Create: `kb/example/kb_obj_tempo.md`
- Create: `kb/example/kb_obj_pensar.md`

- [ ] **Step 1: Add `objections:` block to `tenants/example/tenant.yaml`**

Append to the existing tenant.yaml (after the `guardrails:` block):

```yaml
objections:
  enabled: true
  min_confidence: 0.6
  max_handled_per_lead: 10
  history_window: 4
```

- [ ] **Step 2: Add objections to `tenants/example/treeflows/example.yaml`**

Bump the `version` (e.g., `0.2.0` → `0.3.0`). Then add at the TreeFlow level (after `display_name`):

```yaml
global_objections:
  - id: preco
    kb: kb_obj_preco
    description: "Lead questiona o valor do investimento, acha caro, ou compara com alternativas mais baratas"
  - id: falta_tempo
    kb: kb_obj_tempo
    description: "Lead diz que está sem tempo, agenda cheia, ou que esse não é o momento certo"
  - id: preciso_pensar
    kb: kb_obj_pensar
    description: "Lead pede tempo pra pensar, decidir depois, falar com terceiros antes"
```

Add `handles_objections` to the `qualificacao` node (1 local objection as example):

```yaml
  - id: qualificacao
    prompt: |
      ...existing prompt...
    handles_objections:
      - id: qualif_naotenho_empresa
        kb: kb_obj_preco
        description: "Lead diz que ainda não tem empresa formalizada ou é freelancer começando"
    collects:
      ...
```

(Note: `qualif_naotenho_empresa` reuses `kb_obj_preco` since we don't have a dedicated KB for it. That's fine for the example — adjust later.)

- [ ] **Step 3: Create `kb/example/kb_obj_tempo.md`**

```markdown
# Objeção: Falta de tempo

## Resposta padrão

Entendo, sua agenda já está cheia. A própria mentoria foi desenhada pra caber
em rotinas apertadas — são 2h por semana, com material assíncrono pra você
consumir no seu ritmo. Quem mais entra aqui costuma ser empreendedor sem
tempo livre, e o ROI vem justamente porque a gente foca em projetos que
geram receita, não em teoria.

## Reframe sugerido

A pergunta certa não é "tenho tempo?", e sim "se eu investir 2h/sem nos
próximos 90 dias, isso vai me destravar X?". Se a resposta for sim, a gente
faz acontecer.
```

- [ ] **Step 4: Create `kb/example/kb_obj_pensar.md`**

```markdown
# Objeção: Preciso pensar

## Resposta padrão

Faz sentido. Antes de você pensar, deixa eu te perguntar uma coisa: o que
exatamente você precisa avaliar? Se for o investimento, posso te mostrar
em detalhe o que tá incluso. Se for se é o momento, dá pra a gente desenhar
um plano que respeita a sua janela. O que pesa mais?

## Quando insistir

Não pressione. Combine um follow-up em 48h e pergunte se o lead conseguiu
revisar. Se voltar com objeção concreta (preço, tempo, parceiro), trate
naquele tópico específico em vez de bater na mesma tecla.
```

- [ ] **Step 5: Reindex the example tenant KB (manual)**

```bash
uv run ai-sdr reindex-kb --tenant example
```

Expected: 2 new KB docs indexed (kb_obj_tempo + kb_obj_pensar). The CLI tells you the count.

- [ ] **Step 6: Validate the TreeFlow YAML loads**

```bash
uv run python -c "from pathlib import Path; from ai_sdr.treeflow.loader import TreeFlowLoader; tf = TreeFlowLoader(Path('tenants')).load('example', 'example'); print('OK', tf.version)"
```

Expected: prints `OK 0.3.0` (or whatever version you bumped to).

- [ ] **Step 7: Commit**

```bash
git add tenants/example/tenant.yaml tenants/example/treeflows/example.yaml kb/example/kb_obj_tempo.md kb/example/kb_obj_pensar.md
git commit -m "feat(plan4a t14): example tenant ships objections + 2 new KB fixtures (tempo, pensar)

Plan 4a Task 14"
```

---

## Task 15: CLI — `--no-classifier` flag + classifier result in `--show-extracted`

**Files:**
- Modify: `src/ai_sdr/cli/simulate.py`
- Modify: `tests/integration/test_simulate_*.py` (if there's an existing test; otherwise skip the test step here and rely on T17 for coverage)

- [ ] **Step 1: Inspect `simulate.py`**

```bash
grep -n "show_extracted\|guardrails\|def " src/ai_sdr/cli/simulate.py | head -30
```

Note the typer option pattern.

- [ ] **Step 2: Add `--no-classifier` flag**

In `simulate.py`, locate the typer command and add:

```python
@app.command()
def simulate(
    ...
    no_classifier: bool = typer.Option(
        False,
        "--no-classifier",
        help="Disable the objection classifier for this run (debug).",
    ),
    ...
):
    ...
```

In the call to `compile_treeflow`, pass:

```python
    objections_cfg = tenant_config.objections or ObjectionsConfig()
    if no_classifier:
        objections_cfg = objections_cfg.model_copy(update={"enabled": False})

    graph = compile_treeflow(
        tf,
        tenant_llm=...,
        ...,
        objections=objections_cfg,
    )
```

- [ ] **Step 3: Add classifier result to `--show-extracted` output**

Find where `--show-extracted` already prints state. Add right after the existing `collected:` printout:

```python
        if show_extracted:
            ...existing collected printout...
            handled = state.get("objections_handled", []) or []
            if handled:
                typer.echo("objections_handled:")
                for r in handled:
                    typer.echo(
                        f"  - {r['objection_id']} @ {r['detected_at_node']} (t={r['turn_index']}): {r['quote'][:60]!r}"
                    )
```

- [ ] **Step 4: Sanity-check by running simulate against the example tenant**

```bash
# Make sure docker is up + Anthropic key in secrets.enc.yaml
make up
uv run ai-sdr simulate --tenant example --treeflow example --lead t-test --show-extracted --no-classifier
```

Type `oi` → agent should greet. Type `/quit` to exit.

Then run without `--no-classifier`:

```bash
uv run ai-sdr simulate --tenant example --treeflow example --lead t-test-2 --show-extracted
```

Type `tá muito caro` → classifier should detect, agent responds about price. `objections_handled:` line should appear after the turn.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/cli/simulate.py
git commit -m "feat(plan4a t15): simulate gains --no-classifier flag + prints objections_handled in --show-extracted

Plan 4a Task 15"
```

---

## Task 16: Live Haiku test (`live_llm` marker)

**Files:**
- Create: `tests/integration/test_objection_live.py`

- [ ] **Step 1: Inspect `test_kb_live.py`** for the live_llm pattern

```bash
sed -n '1,40p' tests/integration/test_kb_live.py
```

Note the marker, env-var guard, and key-loading helper.

- [ ] **Step 2: Create the test**

```python
"""Live Haiku classification test (Plan 4a, spec §4.4).

Requires a real ANTHROPIC_API_KEY in the example tenant's secrets. Run via:
    make test-integration -- -m live_llm
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from ai_sdr.llm.factory import build_llm
from ai_sdr.schemas.llm_yaml import LLMConfig
from ai_sdr.schemas.treeflow_yaml import GlobalObjection
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.treeflow.classifier import classify


def _load_secrets() -> dict[str, str]:
    return SopsLoader(Path("tenants")).load("example")


def _haiku_llm():
    cfg = LLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5",
        api_key_ref="secrets/anthropic_key",
    )
    return build_llm(cfg, _load_secrets())


def _example_objections() -> list[GlobalObjection]:
    return [
        GlobalObjection(
            id="preco",
            kb="kb_obj_preco",
            description="Lead questiona o valor do investimento, acha caro, ou compara com alternativas",
        ),
        GlobalObjection(
            id="falta_tempo",
            kb="kb_obj_tempo",
            description="Lead diz que está sem tempo, agenda cheia, ou que esse não é o momento certo",
        ),
        GlobalObjection(
            id="preciso_pensar",
            kb="kb_obj_pensar",
            description="Lead pede tempo pra pensar, decidir depois, falar com terceiros antes",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.live_llm
async def test_classifier_detects_price_objection():
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="tá muito caro pra mim")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id == "preco"
    assert result.confidence >= 0.6


@pytest.mark.asyncio
@pytest.mark.live_llm
async def test_classifier_detects_time_or_decision_for_ambiguous_message():
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="não sei se é a hora certa")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id in {"falta_tempo", "preciso_pensar"}


@pytest.mark.asyncio
@pytest.mark.live_llm
async def test_classifier_returns_null_for_unrelated_message():
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="qual o whatsapp de vocês?")],
        previously_handled=[],
        history_window=4,
    )
    assert result.objection_id is None


@pytest.mark.asyncio
@pytest.mark.live_llm
async def test_classifier_picks_one_for_compound_message():
    result = await classify(
        llm=_haiku_llm(),
        objections=_example_objections(),
        conversation=[HumanMessage(content="tá muito caro E também preciso pensar")],
        previously_handled=[],
        history_window=4,
    )
    # We accept either — multi-objection-per-turn is V2.
    assert result.objection_id in {"preco", "preciso_pensar"}
```

- [ ] **Step 3: Run the live test**

```bash
make test-integration -- -m live_llm -v -k objection_live
```

Expected: 4 PASS. If a test fails, the most likely cause is the classifier prompt needs tuning — adjust `_SYSTEM_TEMPLATE` in `classifier.py` and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_objection_live.py
git commit -m "test(plan4a t16): live Haiku classifier round-trip — 4 scenarios pass against real model

Plan 4a Task 16"
```

---

## Task 17: Simulate acceptance test

**Files:**
- Create: `tests/integration/test_simulate_with_objections.py`

- [ ] **Step 1: Inspect existing simulate tests (if any)**

```bash
ls tests/integration/ | grep simulate
```

If none exist, this is the first one. Use the typer.testing.CliRunner pattern.

- [ ] **Step 2: Create the test**

```python
"""End-to-end acceptance: simulate CLI runs a scripted objection flow (Plan 4a)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ai_sdr.cli.main import app  # adjust import to wherever the typer app lives


@pytest.mark.integration
def test_simulate_handles_price_objection_and_continues():
    """Script the conversation via the same stdin mechanism simulate uses."""
    runner = CliRunner()
    # Pipe a sequence: empty (kicks off agent), then 'tá caro', then 'fechado', then /quit
    result = runner.invoke(
        app,
        ["simulate", "--tenant", "example", "--treeflow", "example", "--lead", "acc-1", "--show-extracted"],
        input="\ntá caro\nfechado\n/quit\n",
    )
    assert result.exit_code == 0
    out = result.stdout
    # Classifier event appears in logs
    assert "objection.classifier.detected" in out or "objection.inline.responded" in out
    # objections_handled printed in --show-extracted output
    assert "objections_handled" in out
    assert "preco" in out
```

(Adapt the input sequence and the assertions to whatever simulate prints — the test is a smoke check, not a strict snapshot.)

- [ ] **Step 3: Run it**

```bash
uv run pytest tests/integration/test_simulate_with_objections.py -v
```

Expected: PASS. This test calls a real LLM (the main one), so make sure ANTHROPIC_API_KEY is loaded. If you want to keep it pure-mock, swap simulate's compile path to accept a `classify_fn` override — but the simplest acceptance test is to just run it end-to-end.

If running against a real LLM is too flaky for CI, mark it `@pytest.mark.live_llm` and gate via the existing marker.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_simulate_with_objections.py
git commit -m "test(plan4a t17): simulate CLI scripted run with objection trigger — acceptance smoke

Plan 4a Task 17"
```

---

## Task 18: CLAUDE.md authoring docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add an "Objection Classifier (Plan 4a)" section**

Add after the existing "Guardrails (Plan 3)" section in `CLAUDE.md`:

```markdown
## Objection Classifier (Plan 4a)

- Tenant config: `tenant.yaml > objections` block — `enabled`, `min_confidence` (default 0.6), `max_handled_per_lead` (default 10), `history_window` (default 4 messages).
- Schema: every `NodeObjection` / `GlobalObjection` requires `id`, `kb`, `description` (10-300 chars). The description is what the classifier sees — be specific in PT-BR. `as_subnode: <node_id>` is optional; when set, the classifier dispatches to the referenced full Node (which must declare a transition to `BACK_TO_ORIGIN`).
- Reuses `tenant.llm.classifier` (Haiku) — no new LLM config needed.
- Topology: compiler emits `{node_id}:classifier` and (when N has inline objections) `{node_id}:inline` as synthetic LangGraph nodes. `state.current_node` stays as the TreeFlow node id (never the synthetic names).
- Kill switch: `tenant.objections.enabled=false` makes every `:classifier` a passthrough (zero Haiku call).
- CLI: `ai-sdr simulate ... --no-classifier` to disable for a single run; `--show-extracted` prints `objections_handled`.
- Failure modes (all degrade to "no match → main", never block the turn): Haiku raise, structured-output validation error, hallucinated objection_id, KB empty, KB missing, BACK_TO_ORIGIN with no origin (falls back to entry_node).
- Events emitted (structlog): `objection.classifier.{skipped,detected,no_match,error,invalid_output,hallucinated_id}`, `objection.inline.responded`, `objection.subnode.{entered,exited,orphan_return}`, `objection.kb.{empty,missing}`, `objection.threshold.exceeded`.
- TreeFlow version bump required when adding objections to an existing TreeFlow YAML (runtime refuses to re-publish same version with different hash).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(plan4a t18): CLAUDE.md gains Objection Classifier authoring guide

Plan 4a Task 18"
```

---

## Done criteria

- [ ] `make lint && make format && make type && make test-unit` clean.
- [ ] `make test-integration` clean (with docker up).
- [ ] `make test-integration -- -m live_llm` clean (with ANTHROPIC_API_KEY in tenants/example/secrets.enc.yaml).
- [ ] `uv run ai-sdr simulate --tenant example --treeflow example --lead manual-test --show-extracted` works; typing "tá caro" deflects, typing "qual o whats?" goes to main.
- [ ] All 18 commits land on branch `dev/nicolas-p4`, ready for PR.

---

## References

- Spec: [`docs/superpowers/specs/2026-05-24-plan4a-objection-classifier-design.md`](../specs/2026-05-24-plan4a-objection-classifier-design.md)
- Parent spec: [`docs/superpowers/specs/2026-05-21-ai-sdr-design.md`](../specs/2026-05-21-ai-sdr-design.md) §4.4
- Plan 3 (KB + Guardrails): [`docs/superpowers/plans/2026-05-23-kb-and-guardrails.md`](./2026-05-23-kb-and-guardrails.md)
- Plan 2 (TreeFlow Engine): [`docs/superpowers/plans/2026-05-22-treeflow-engine-langgraph.md`](./2026-05-22-treeflow-engine-langgraph.md)
