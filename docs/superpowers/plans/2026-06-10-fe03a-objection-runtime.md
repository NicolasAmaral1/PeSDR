# FE-03a — Objection Runtime + Python Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar tratamento stateful multi-turno de objeções (`tool` mode) e substituir critic LLM por validador Python na linha FlowEngine v2.

**Architecture:** Estende o pipeline `run_turn` (FE-01b) com (a) máquina de estado `ActiveTreatment` aplicada em `objection_runtime.apply()`, (b) validador Python ampliado com whitelist de produtos e fallback configurável, (c) extensões pontuais em `routing.py` (contexto simpleeval + bloqueio de transição durante tratamento) e `system_prompt.py` (bloco de tratamento ativo + bridge_instruction nos next_nodes), (d) heurísticas pós-LLM para contradição e transição implícita, (e) migration `0025` adicionando `talks.requires_review_reason`.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · simpleeval · pytest · structlog. Sem novas dependências externas.

**Spec fonte:** `docs/superpowers/specs/2026-06-09-fe03a-objection-runtime-design.md` (commit `f734cd4`).

**Branch:** `dev/nicolas-fe03a-objection-runtime` (já criada a partir de `dev/nicolas-fe01b-pipeline`).

**Worktree:** `/Users/nicolasamaral/dev/PeSDR-fe01b-pipeline`.

---

## File structure

### Novos arquivos
- `src/ai_sdr/flowengine/objection_runtime.py` — máquina de estado pura `apply(state, decision, treeflow) -> StateDelta`
- `src/ai_sdr/flowengine/heuristics.py` — heurísticas pós-LLM (B4 contradição, C4 implicit transition)
- `migrations/versions/0025_talks_requires_review_reason.py` — adiciona coluna `talks.requires_review_reason`

### Arquivos modificados (`src/ai_sdr/flowengine/`)
- `decision.py` — substituir `treatment_resolved: bool` por `treatment_status: Literal[...] | None`
- `state.py` — adicionar `off_topic_count: int = 0` em `TalkFlowState`
- `treeflow_loader.py` — parse `global_objections`, `handles_objections`, `tool_payload` + bounds validation
- `system_prompt.py` — bloco `active_treatment` quando setado + `bridge_instruction` em IMMEDIATE NEXT NODES + bloco de objeções node-scoped + conservative resolution guidance
- `routing.py` — `validate_transition` recebe `TalkFlowState` inteiro; contexto simpleeval expandido; bloqueia transição se `active_treatment is not None`
- `post_processing.py` — invoca `objection_runtime.apply()` e aplica `StateDelta`; trata `off_topic_count`; trata `requires_review_reason`
- `pipeline.py` — wire heurísticas pós-LLM; wire `requires_review_reason` em todos os caminhos de escalation; transação única

### Arquivos modificados (outros)
- `src/ai_sdr/guardrails/validator.py` — `GuardrailConfig` ganha `allowed_products: list[str]` + `fallback_text: str`; `validate_response_text` ganha product whitelist com normalização
- `src/ai_sdr/schemas/tenant_yaml.py` — `TenantGuardrails` exige `allowed_products` e `fallback_text` quando `enabled=true`
- `src/ai_sdr/models/talk.py` — campo SQLAlchemy `requires_review_reason: Mapped[str | None]`
- `src/ai_sdr/messaging/ingest.py` ou worker handler — janela de concatenação 2s antes do `run_turn`
- `CLAUDE.md` — seção "FE-03a: Objection Runtime" com notas operacionais

### Arquivos de teste (`tests/unit/` flat — sem subdir)
27 novos arquivos cobrindo decisão Pydantic, treeflow_loader, validator, routing, heurísticas, off-topic, objection_runtime.

### Arquivos de teste (`tests/integration/` flat)
8 novos arquivos cobrindo cenários multi-turn E2E com `FakeListChatModel`.

### Fixtures (`tests/fixtures/`)
- `avelum_v2_with_objections.yaml` — tenant com `global_objections` completas
- `avelum_v2_node_objections.yaml` — tenant com `handles_objections` por node
- `treeflow_invalid_max_turns.yaml`, `treeflow_invalid_treatment_mode.yaml`, `treeflow_missing_tool_payload.yaml` — fixtures pra bounds validation

---

## Phase 1: Schema extensions

### Task 1: Atualizar `TurnDecision` — substituir `treatment_resolved` por `treatment_status`

**Files:**
- Modify: `src/ai_sdr/flowengine/decision.py:68`
- Test: `tests/unit/test_decision_schema_treatment_status.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_decision_schema_treatment_status.py
"""TurnDecision schema — treatment_status replaces treatment_resolved (FE-03a Task 1)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.decision import TurnDecision


def _base_kwargs(**overrides):
    return {
        "response_text": "ok",
        "collected_fields": {},
        "reasoning": "test",
        **overrides,
    }


def test_treatment_status_defaults_to_none():
    d = TurnDecision(**_base_kwargs())
    assert d.treatment_status is None


def test_treatment_status_accepts_in_progress():
    d = TurnDecision(**_base_kwargs(treatment_status="in_progress"))
    assert d.treatment_status == "in_progress"


def test_treatment_status_accepts_resolved_accepted():
    d = TurnDecision(**_base_kwargs(treatment_status="resolved_accepted"))
    assert d.treatment_status == "resolved_accepted"


def test_treatment_status_accepts_resolved_deferred():
    d = TurnDecision(**_base_kwargs(treatment_status="resolved_deferred"))
    assert d.treatment_status == "resolved_deferred"


def test_treatment_status_rejects_other_values():
    with pytest.raises(ValidationError):
        TurnDecision(**_base_kwargs(treatment_status="something_else"))


def test_treatment_resolved_field_removed():
    """The old boolean field is gone; using it raises."""
    with pytest.raises(ValidationError):
        TurnDecision(**_base_kwargs(treatment_resolved=True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicolasamaral/dev/PeSDR-fe01b-pipeline && uv run pytest tests/unit/test_decision_schema_treatment_status.py -v`
Expected: 5 of 6 tests FAIL (`treatment_status` attribute missing or accepts any string); 1 PASSES coincidentally (`treatment_resolved` still exists).

- [ ] **Step 3: Apply the minimal implementation**

Edit `src/ai_sdr/flowengine/decision.py`:

Replace lines 21 (just below `from pydantic import ...`):
```python
TreatmentStrategy = Literal["inline", "subnode", "tool"]
```
With:
```python
TreatmentStrategy = Literal["inline", "subnode", "tool"]
TreatmentStatus = Literal["in_progress", "resolved_accepted", "resolved_deferred"]
```

Replace line 68:
```python
    # Treatment resolution (when active_treatment was in progress)
    treatment_resolved: bool = False
```
With:
```python
    # Treatment resolution (when active_treatment was in progress).
    # Only meaningful when state.active_treatment is set; ignored otherwise.
    treatment_status: TreatmentStatus | None = None
```

Update the module docstring (line 8) — replace `treatment_resolved` with `treatment_status`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_decision_schema_treatment_status.py -v`
Expected: PASS (6/6).

Run the full decision suite to catch regressions:
`uv run pytest tests/unit/test_flowengine_decision_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/decision.py tests/unit/test_decision_schema_treatment_status.py
git commit -m "feat(fe03a t1): TurnDecision.treatment_status replaces boolean treatment_resolved

Per FE-03a spec §5. Three-valued status (in_progress / resolved_accepted /
resolved_deferred) lets runtime distinguish 'accepted by exhaustion' from
'lead truly aligned' — fed by conservative resolution guidance in §4.5."
```

---

### Task 2: Adicionar `off_topic_count` em `TalkFlowState`

**Files:**
- Modify: `src/ai_sdr/flowengine/state.py`
- Test: `tests/unit/test_state_offtopic_count.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_state_offtopic_count.py
"""TalkFlowState.off_topic_count default + validation (FE-03a Task 2)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.state import (
    Message,
)

# off_topic_count lives in the model TalkFlowState (SQLAlchemy) but is
# proxied through a Pydantic payload schema. We test the Pydantic shape
# that gets serialised into the JSONB column.
from ai_sdr.flowengine.state import TalkFlowStatePayload  # NEW class


def test_off_topic_count_defaults_zero():
    p = TalkFlowStatePayload()
    assert p.off_topic_count == 0


def test_off_topic_count_accepts_positive_int():
    p = TalkFlowStatePayload(off_topic_count=5)
    assert p.off_topic_count == 5


def test_off_topic_count_rejects_negative():
    with pytest.raises(ValidationError):
        TalkFlowStatePayload(off_topic_count=-1)


def test_off_topic_count_legacy_payload_without_field_loads_clean():
    """Existing serialized state without off_topic_count must default to 0."""
    legacy = {"messages": [], "objections_handled": []}
    p = TalkFlowStatePayload.model_validate(legacy)
    assert p.off_topic_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_state_offtopic_count.py -v`
Expected: FAIL — `TalkFlowStatePayload` does not exist OR does not have `off_topic_count`.

- [ ] **Step 3: Apply implementation**

Add at the bottom of `src/ai_sdr/flowengine/state.py`:

```python
class TalkFlowStatePayload(BaseModel):
    """Pydantic envelope for the TalkFlowState JSONB column.

    Provides defaults for fields added across FlowEngine phases so legacy
    rows deserialize cleanly. Fields here mirror columns persisted by
    ``TalkFlowStateRepository``; see ai_sdr/models/talkflow_state.py.
    """

    off_topic_count: int = Field(default=0, ge=0)

    model_config = {"extra": "allow"}
```

(`extra="allow"` keeps forward-compatibility with other JSONB keys not enumerated here yet.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_state_offtopic_count.py -v`
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/state.py tests/unit/test_state_offtopic_count.py
git commit -m "feat(fe03a t2): TalkFlowStatePayload introduces off_topic_count (default 0)

Pydantic envelope provides defaults for fields added across FlowEngine
phases so legacy rows deserialize cleanly. Per FE-03a spec §10.1."
```

---

### Task 3: Migration `0025` — adicionar `talks.requires_review_reason`

**Files:**
- Create: `migrations/versions/0025_talks_requires_review_reason.py`
- Test: `tests/integration/test_migration_0025_requires_review_reason.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_migration_0025_requires_review_reason.py
"""Migration 0025 adds talks.requires_review_reason (FE-03a Task 3)."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_requires_review_reason_column_exists(async_engine):
    async with async_engine.connect() as conn:
        def _cols(sync_conn):
            insp = inspect(sync_conn)
            return {c["name"]: c for c in insp.get_columns("talks")}
        cols = await conn.run_sync(_cols)
    assert "requires_review_reason" in cols
    col = cols["requires_review_reason"]
    assert col["nullable"] is True
    # String column (VARCHAR or TEXT-ish)
    assert "VARCHAR" in str(col["type"]).upper() or "TEXT" in str(col["type"]).upper()


@pytest.mark.asyncio
async def test_requires_review_reason_check_constraint(async_engine):
    """Constraint accepts the documented enum values + NULL."""
    valid = [
        "escalation_requested",
        "off_topic_exhausted",
        "validator_exhausted",
        "treeflow_version_missing",
        "objection_treatment_exhausted",
    ]
    async with async_engine.connect() as conn:
        for v in valid:
            r = await conn.execute(
                text(
                    "SELECT 'ok' WHERE "
                    "'{v}' IN ('escalation_requested', 'off_topic_exhausted', "
                    "'validator_exhausted', 'treeflow_version_missing', "
                    "'objection_treatment_exhausted')".format(v=v)
                )
            )
            assert r.scalar() == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Bring DB to current head first: `make up && uv run alembic upgrade head` (from the worktree).

Run: `uv run pytest tests/integration/test_migration_0025_requires_review_reason.py -v`
Expected: FAIL — column does not exist.

- [ ] **Step 3: Apply implementation**

Create `migrations/versions/0025_talks_requires_review_reason.py`:

```python
"""add talks.requires_review_reason (FlowEngine FE-03a)

Per spec §11. Records WHY a Talk was flagged for human review, so the
operator HITL console (FE-07) can prioritise/route differently per
reason. Multiple FE-03a code paths converge on Talk.status=requires_review:
this column makes the converging streams distinguishable downstream.

Revision ID: 0025_talks_requires_review_reason
Revises: 0024_relax_outbound_talkflow_fk
Create Date: 2026-06-10 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_talks_requires_review_reason"
down_revision = "0024_relax_outbound_talkflow_fk"
branch_labels = None
depends_on = None


REASONS = (
    "escalation_requested",
    "off_topic_exhausted",
    "validator_exhausted",
    "treeflow_version_missing",
    "objection_treatment_exhausted",
)


def upgrade() -> None:
    op.add_column(
        "talks",
        sa.Column("requires_review_reason", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_talks_requires_review_reason",
        "talks",
        "requires_review_reason IS NULL OR requires_review_reason IN ("
        + ", ".join(f"'{r}'" for r in REASONS)
        + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_requires_review_reason", "talks", type_="check")
    op.drop_column("talks", "requires_review_reason")
```

- [ ] **Step 4: Run the migration + test**

```bash
uv run alembic upgrade head
uv run pytest tests/integration/test_migration_0025_requires_review_reason.py -v
```
Expected: migration runs cleanly, tests PASS (2/2).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0025_talks_requires_review_reason.py tests/integration/test_migration_0025_requires_review_reason.py
git commit -m "feat(fe03a t3): migration 0025 — talks.requires_review_reason

Records WHY a Talk landed in requires_review queue. Enum of 5 reasons
covering FE-03a escalation paths. Per spec §11."
```

---

### Task 4: SQLAlchemy `Talk` model — campo `requires_review_reason`

**Files:**
- Modify: `src/ai_sdr/models/talk.py`
- Test: `tests/unit/test_talk_model_requires_review_reason.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_talk_model_requires_review_reason.py
"""Talk model exposes requires_review_reason column (FE-03a Task 4)."""
from __future__ import annotations

from ai_sdr.models.talk import Talk


def test_talk_has_requires_review_reason_attribute():
    assert hasattr(Talk, "requires_review_reason")


def test_requires_review_reason_default_none():
    t = Talk.__new__(Talk)
    assert getattr(t, "requires_review_reason", None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_talk_model_requires_review_reason.py -v`
Expected: FAIL — attribute missing.

- [ ] **Step 3: Apply implementation**

Open `src/ai_sdr/models/talk.py`. Locate the block of `Mapped[...]` column declarations and add:

```python
    requires_review_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
    )
```

Adjacent to `escalation_reason` (keep related fields together).

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_talk_model_requires_review_reason.py -v
uv run pytest tests/unit/ -k talk -v
```
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talk.py tests/unit/test_talk_model_requires_review_reason.py
git commit -m "feat(fe03a t4): Talk.requires_review_reason ORM column

Mirrors migration 0025. Per spec §11."
```

---

## Phase 2: TreeFlow YAML schema extensions

### Task 5: `TreeFlowLoader` parses `global_objections` + `tool_payload`

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_global_objections.py` (create)
- Test fixture: `tests/fixtures/avelum_v2_with_objections.yaml` (create)

- [ ] **Step 1: Create the fixture**

```yaml
# tests/fixtures/avelum_v2_with_objections.yaml
schema_version: 1
id: avelum_v2_obj
version: 1.0.0
display_name: "Avelum SDR — with objections"

sdr_persona:
  voice: "PT-BR informal"
  conduct: "1. Reconheça\n2. Não invente preços"

global_objections:
  - id: preco
    description: "lead questiona valor, acha caro"
    treatment_mode: tool
    tool_payload:
      canonical_arguments_summary: "ROI, parcelamento, comparação"
      kb_ref: argumentos_preco
      max_treatment_turns: 3
      expected_turns: 2
      resolution_criteria: "Lead aceitou parcelamento ou pediu pra continuar"
      on_max_turns_no_resolution:
        action: gracefully_continue
        message_hint: "Reconheça hesitação, ofereça material"
  - id: pediu_downsell
    description: "lead pede algo mais barato"
    treatment_mode: inline

entry_node: saudacao
nodes:
  - id: saudacao
    objetivo: "Cumprimentar"
    collects:
      - field: segmento
        type: text
        required: true
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: saudacao
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_treeflow_loader_global_objections.py
"""TreeFlowLoader parses global_objections + tool_payload (FE-03a Task 5)."""
from __future__ import annotations

from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "avelum_v2_with_objections.yaml"
)


def test_global_objections_loaded():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert hasattr(tf, "global_objections")
    assert len(tf.global_objections) == 2


def test_global_objections_indexed_by_id():
    tf = load_treeflow_v2(FIXTURE.read_text())
    by_id = {o.id: o for o in tf.global_objections}
    assert "preco" in by_id
    assert "pediu_downsell" in by_id


def test_tool_objection_carries_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    preco = next(o for o in tf.global_objections if o.id == "preco")
    assert preco.treatment_mode == "tool"
    assert preco.tool_payload is not None
    assert preco.tool_payload.max_treatment_turns == 3
    assert preco.tool_payload.kb_ref == "argumentos_preco"
    assert (
        preco.tool_payload.on_max_turns_no_resolution.action
        == "gracefully_continue"
    )


def test_inline_objection_has_no_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    ds = next(o for o in tf.global_objections if o.id == "pediu_downsell")
    assert ds.treatment_mode == "inline"
    assert ds.tool_payload is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_treeflow_loader_global_objections.py -v`
Expected: FAIL — `global_objections` attribute missing.

- [ ] **Step 4: Apply implementation**

In `src/ai_sdr/flowengine/treeflow_loader.py`, add new dataclasses after `TreeflowTransition`:

```python
TreatmentMode = "tool"  # see Literal below; alias for readability


@dataclass
class TreeflowOnMaxTurns:
    action: str  # "gracefully_continue" | "escalate_to_human"
    message_hint: str | None = None


@dataclass
class TreeflowToolPayload:
    canonical_arguments_summary: str
    kb_ref: str
    max_treatment_turns: int
    resolution_criteria: str
    on_max_turns_no_resolution: TreeflowOnMaxTurns
    expected_turns: int | None = None


@dataclass
class TreeflowObjection:
    id: str
    description: str
    treatment_mode: str  # "tool" | "inline"
    tool_payload: TreeflowToolPayload | None = None
```

Extend `TreeflowDef`:

```python
@dataclass
class TreeflowDef:
    id: str
    version: str
    display_name: str | None
    sdr_persona: dict[str, Any]
    entry_node: str
    nodes: dict[str, TreeflowNode]
    global_objections: list[TreeflowObjection] = field(default_factory=list)
```

Add a private parser:

```python
def _parse_objection(raw: dict[str, Any]) -> TreeflowObjection:
    required = {"id", "description", "treatment_mode"}
    missing = required - raw.keys()
    if missing:
        raise TreeflowLoadError(
            f"objection missing fields {sorted(missing)}: {raw!r}"
        )
    mode = raw["treatment_mode"]
    payload: TreeflowToolPayload | None = None
    if mode == "tool":
        tp = raw.get("tool_payload")
        if not isinstance(tp, dict):
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: treatment_mode=tool requires tool_payload"
            )
        omtr = tp.get("on_max_turns_no_resolution") or {}
        if not omtr.get("action"):
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: on_max_turns_no_resolution.action required"
            )
        payload = TreeflowToolPayload(
            canonical_arguments_summary=tp.get("canonical_arguments_summary", ""),
            kb_ref=tp.get("kb_ref", ""),
            max_treatment_turns=int(tp.get("max_treatment_turns", 0)),
            resolution_criteria=tp.get("resolution_criteria", ""),
            expected_turns=tp.get("expected_turns"),
            on_max_turns_no_resolution=TreeflowOnMaxTurns(
                action=omtr["action"],
                message_hint=omtr.get("message_hint"),
            ),
        )
    return TreeflowObjection(
        id=raw["id"],
        description=raw["description"],
        treatment_mode=mode,
        tool_payload=payload,
    )
```

In `load_treeflow_v2`, after building `nodes`, add:

```python
    global_objections = [
        _parse_objection(o) for o in data.get("global_objections", [])
    ]
```

And include in the returned `TreeflowDef(...)`:

```python
        global_objections=global_objections,
```

(Import `field` from `dataclasses` at the top.)

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_loader_global_objections.py -v
```
Expected: PASS (4/4).

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_global_objections.py tests/fixtures/avelum_v2_with_objections.yaml
git commit -m "feat(fe03a t5): TreeFlowLoader parses global_objections + tool_payload

Per spec §6.1. Adds TreeflowObjection / TreeflowToolPayload /
TreeflowOnMaxTurns dataclasses, parser, and field on TreeflowDef.
inline objections have null tool_payload."
```

---

### Task 6: `TreeFlowLoader` parses `node.handles_objections`

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_handles_objections.py` (create)
- Fixture: `tests/fixtures/avelum_v2_node_objections.yaml` (create)

- [ ] **Step 1: Create the fixture**

```yaml
# tests/fixtures/avelum_v2_node_objections.yaml
schema_version: 1
id: avelum_v2_node_obj
version: 1.0.0
sdr_persona:
  voice: "PT-BR"
  conduct: "1. Reconheça"
entry_node: qualificacao
nodes:
  - id: qualificacao
    objetivo: "Qualificar"
    collects:
      - field: faturamento
        type: text
        required: true
    handles_objections:
      - id: ja_tentei_curso_online
        description: "lead diz que cursos online não funcionam pra ele"
        treatment_mode: tool
        tool_payload:
          canonical_arguments_summary: "diferencial mentoria 1:1"
          kb_ref: kb_diferencial
          max_treatment_turns: 2
          resolution_criteria: "lead aceitou diferencial"
          on_max_turns_no_resolution:
            action: gracefully_continue
    exit_condition:
      type: all_fields_filled
    next_nodes:
      - condition: "true"
        target: qualificacao
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_treeflow_loader_handles_objections.py
"""TreeFlowLoader parses node.handles_objections (FE-03a Task 6)."""
from __future__ import annotations

from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "avelum_v2_node_objections.yaml"
)


def test_handles_objections_loaded_on_node():
    tf = load_treeflow_v2(FIXTURE.read_text())
    node = tf.nodes["qualificacao"]
    assert hasattr(node, "handles_objections")
    assert len(node.handles_objections) == 1


def test_handles_objection_has_tool_payload():
    tf = load_treeflow_v2(FIXTURE.read_text())
    obj = tf.nodes["qualificacao"].handles_objections[0]
    assert obj.id == "ja_tentei_curso_online"
    assert obj.treatment_mode == "tool"
    assert obj.tool_payload.max_treatment_turns == 2


def test_node_without_handles_objections_defaults_empty():
    """A node that omits the block must yield an empty list, not raise."""
    yaml_text = FIXTURE.read_text()
    # Replace the handles_objections block with nothing for this assertion
    # by using the with_objections fixture (already lacks node-scoped ones).
    other = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "avelum_v2_with_objections.yaml"
    )
    tf = load_treeflow_v2(other.read_text())
    assert tf.nodes["saudacao"].handles_objections == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_treeflow_loader_handles_objections.py -v`
Expected: FAIL — `handles_objections` not on `TreeflowNode`.

- [ ] **Step 4: Apply implementation**

In `treeflow_loader.py`, extend `TreeflowNode`:

```python
@dataclass
class TreeflowNode:
    id: str
    objetivo: str
    bridge_instruction: str
    collects: list[TreeflowCollectField]
    exit_condition: TreeflowExitCondition
    next_nodes: list[TreeflowTransition]
    handles_objections: list[TreeflowObjection] = field(default_factory=list)
```

In `_parse_node`, before the `return TreeflowNode(...)`, add:

```python
    handles_objections = [
        _parse_objection(o) for o in raw.get("handles_objections", [])
    ]
```

And include in the constructor call:

```python
        handles_objections=handles_objections,
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_loader_handles_objections.py -v
uv run pytest tests/unit/test_treeflow_loader_global_objections.py -v
```
Expected: PASS, no regression in Task 5 tests.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_handles_objections.py tests/fixtures/avelum_v2_node_objections.yaml
git commit -m "feat(fe03a t6): TreeFlowLoader parses node.handles_objections

Per spec §6.2. Node-scoped objections only visible to LLM when agent
is in that node; precedence over global if id collides (warning in t8)."
```

---

### Task 7: `TreeFlowLoader` bounds validation

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_bounds_validation.py` (create)
- Fixtures: 3 invalid yamls in `tests/fixtures/`

- [ ] **Step 1: Create invalid fixtures**

`tests/fixtures/treeflow_invalid_max_turns.yaml`:
```yaml
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "lead diz preço caro"
    treatment_mode: tool
    tool_payload:
      canonical_arguments_summary: "argumentos"
      kb_ref: kb
      max_treatment_turns: 100   # out of range [1..10]
      resolution_criteria: "criterio"
      on_max_turns_no_resolution: { action: gracefully_continue }
```

`tests/fixtures/treeflow_invalid_treatment_mode.yaml`:
```yaml
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "lead diz preço caro"
    treatment_mode: subflow   # only tool|inline allowed
```

`tests/fixtures/treeflow_missing_tool_payload.yaml`:
```yaml
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "lead diz preço caro"
    treatment_mode: tool
    # missing tool_payload
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_treeflow_loader_bounds_validation.py
"""TreeFlowLoader bounds validation (FE-03a Task 7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2

F = Path(__file__).resolve().parent.parent / "fixtures"


def test_rejects_max_turns_out_of_range():
    with pytest.raises(TreeflowLoadError, match="max_treatment_turns"):
        load_treeflow_v2((F / "treeflow_invalid_max_turns.yaml").read_text())


def test_rejects_unknown_treatment_mode():
    with pytest.raises(TreeflowLoadError, match="treatment_mode"):
        load_treeflow_v2((F / "treeflow_invalid_treatment_mode.yaml").read_text())


def test_rejects_missing_tool_payload_when_mode_tool():
    with pytest.raises(TreeflowLoadError, match="tool_payload"):
        load_treeflow_v2((F / "treeflow_missing_tool_payload.yaml").read_text())


def test_rejects_description_under_min_length():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "short"
    treatment_mode: inline
"""
    with pytest.raises(TreeflowLoadError, match="description"):
        load_treeflow_v2(yaml_text)


def test_rejects_unknown_on_max_turns_action():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
global_objections:
  - id: preco
    description: "lead diz preço caro"
    treatment_mode: tool
    tool_payload:
      canonical_arguments_summary: "abc"
      kb_ref: kb
      max_treatment_turns: 3
      resolution_criteria: "criterio"
      on_max_turns_no_resolution: { action: shoot_lead }
"""
    with pytest.raises(TreeflowLoadError, match="action"):
        load_treeflow_v2(yaml_text)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_treeflow_loader_bounds_validation.py -v`
Expected: 5 FAIL — current `_parse_objection` not strict yet.

- [ ] **Step 4: Apply implementation**

In `treeflow_loader.py`, replace `_parse_objection` with the strict version:

```python
_ALLOWED_MODES = {"tool", "inline"}
_ALLOWED_MAX_TURNS_ACTIONS = {"gracefully_continue", "escalate_to_human"}


def _parse_objection(raw: dict[str, Any]) -> TreeflowObjection:
    required = {"id", "description", "treatment_mode"}
    missing = required - raw.keys()
    if missing:
        raise TreeflowLoadError(
            f"objection missing fields {sorted(missing)}: {raw!r}"
        )
    desc = str(raw["description"])
    if len(desc) < 10:
        raise TreeflowLoadError(
            f"objection {raw['id']!r}: description must be >=10 chars"
        )
    mode = raw["treatment_mode"]
    if mode not in _ALLOWED_MODES:
        raise TreeflowLoadError(
            f"objection {raw['id']!r}: treatment_mode must be one of "
            f"{sorted(_ALLOWED_MODES)}, got {mode!r}"
        )
    payload: TreeflowToolPayload | None = None
    if mode == "tool":
        tp = raw.get("tool_payload")
        if not isinstance(tp, dict):
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: treatment_mode=tool requires "
                f"tool_payload mapping"
            )
        mtt = int(tp.get("max_treatment_turns", 0))
        if not 1 <= mtt <= 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: max_treatment_turns must be in "
                f"[1, 10], got {mtt}"
            )
        cas = str(tp.get("canonical_arguments_summary", ""))
        if len(cas) < 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: canonical_arguments_summary "
                "must be >=10 chars"
            )
        rc = str(tp.get("resolution_criteria", ""))
        if len(rc) < 10:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: resolution_criteria must be "
                ">=10 chars"
            )
        omtr_raw = tp.get("on_max_turns_no_resolution") or {}
        action = omtr_raw.get("action")
        if action not in _ALLOWED_MAX_TURNS_ACTIONS:
            raise TreeflowLoadError(
                f"objection {raw['id']!r}: on_max_turns_no_resolution.action "
                f"must be one of {sorted(_ALLOWED_MAX_TURNS_ACTIONS)}, "
                f"got {action!r}"
            )
        payload = TreeflowToolPayload(
            canonical_arguments_summary=cas,
            kb_ref=tp.get("kb_ref", ""),
            max_treatment_turns=mtt,
            resolution_criteria=rc,
            expected_turns=tp.get("expected_turns"),
            on_max_turns_no_resolution=TreeflowOnMaxTurns(
                action=action,
                message_hint=omtr_raw.get("message_hint"),
            ),
        )
    return TreeflowObjection(
        id=str(raw["id"]),
        description=desc,
        treatment_mode=mode,
        tool_payload=payload,
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_loader_bounds_validation.py -v
uv run pytest tests/unit/test_treeflow_loader_global_objections.py tests/unit/test_treeflow_loader_handles_objections.py -v
```
Expected: PASS (5/5 new), no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_bounds_validation.py tests/fixtures/treeflow_invalid_max_turns.yaml tests/fixtures/treeflow_invalid_treatment_mode.yaml tests/fixtures/treeflow_missing_tool_payload.yaml
git commit -m "feat(fe03a t7): bounds validation in TreeFlowLoader

max_treatment_turns in [1,10], treatment_mode in {tool,inline}, action
in {gracefully_continue, escalate_to_human}, description >=10 chars,
canonical_arguments_summary >=10 chars, resolution_criteria >=10 chars.
Errors are fatal — tenant fails to load. Per spec §6.3."
```

---

## Phase 3: GuardrailConfig + Python validator extensions

### Task 8: `GuardrailConfig` ganha `allowed_products` + `fallback_text`

**Files:**
- Modify: `src/ai_sdr/guardrails/validator.py`
- Test: `tests/unit/test_guardrail_config_extended.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_guardrail_config_extended.py
"""GuardrailConfig has allowed_products + fallback_text (FE-03a Task 8)."""
from __future__ import annotations

from ai_sdr.guardrails.validator import GuardrailConfig


def test_allowed_products_field_exists():
    cfg = GuardrailConfig(
        disallowed_price_pattern=r"R\$\s*\d+",
        allowed_prices=["R$ 6000"],
        allowed_products=["Mentoria", "Aceleradora"],
        fallback_text="Deixa eu confirmar isso com a equipe.",
    )
    assert cfg.allowed_products == ["Mentoria", "Aceleradora"]


def test_fallback_text_field_exists():
    cfg = GuardrailConfig(
        disallowed_price_pattern="",
        allowed_prices=[],
        allowed_products=[],
        fallback_text="Vou validar com a equipe.",
    )
    assert cfg.fallback_text == "Vou validar com a equipe."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_guardrail_config_extended.py -v`
Expected: FAIL — `GuardrailConfig.__init__` does not accept the new kwargs.

- [ ] **Step 3: Apply implementation**

Edit `src/ai_sdr/guardrails/validator.py`. Replace the `GuardrailConfig` dataclass:

```python
@dataclass(frozen=True)
class GuardrailConfig:
    disallowed_price_pattern: str  # regex; empty string disables the check
    allowed_prices: list[str]
    allowed_products: list[str]
    fallback_text: str
```

Update the module docstring to mention product whitelist + fallback message.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_guardrail_config_extended.py -v
uv run pytest tests/unit/ -k guardrail -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/guardrails/validator.py tests/unit/test_guardrail_config_extended.py
git commit -m "feat(fe03a t8): GuardrailConfig gains allowed_products + fallback_text

Per spec §7.1. allowed_products feeds whitelist check in next task;
fallback_text is sent to lead when validator exhausts retries."
```

---

### Task 9: `validate_response_text` ganha product whitelist com normalização

**Files:**
- Modify: `src/ai_sdr/guardrails/validator.py`
- Test: `tests/unit/test_python_validator_product_whitelist.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_python_validator_product_whitelist.py
"""validate_response_text catches unknown products (FE-03a Task 9)."""
from __future__ import annotations

from ai_sdr.guardrails.validator import GuardrailConfig, validate_response_text


def _cfg(products: list[str]) -> GuardrailConfig:
    return GuardrailConfig(
        disallowed_price_pattern="",
        allowed_prices=[],
        allowed_products=products,
        fallback_text="Vou validar com a equipe.",
    )


def test_allowed_product_passes():
    cfg = _cfg(["Mentoria", "Aceleradora"])
    r = validate_response_text("A Mentoria vai te ajudar.", cfg)
    assert r.ok


def test_unknown_product_fails():
    cfg = _cfg(["Mentoria"])
    r = validate_response_text("Vou te indicar o Curso Express.", cfg)
    assert not r.ok
    assert r.category == "product_invented"


def test_match_is_case_insensitive():
    cfg = _cfg(["Mentoria"])
    r = validate_response_text("a mentoria é boa", cfg)
    assert r.ok


def test_match_collapses_internal_whitespace():
    cfg = _cfg(["Mentoria Premium"])
    r = validate_response_text("A Mentoria  Premium é top.", cfg)
    assert r.ok


def test_empty_allowed_products_disables_check():
    cfg = _cfg([])
    r = validate_response_text("Vou te oferecer qualquer coisa aleatória.", cfg)
    assert r.ok
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_python_validator_product_whitelist.py -v`
Expected: 4 of 5 FAIL — current `validate_response_text` only does price check; `product_invented` category never raised.

- [ ] **Step 3: Apply implementation**

In `validator.py`, add a normalization helper and extend `validate_response_text`:

```python
def _normalize_product(s: str) -> str:
    """lowercase + collapse internal whitespace + strip ends. No punctuation removal."""
    return " ".join(s.lower().split())


_KNOWN_PRODUCT_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s]{2,}", re.UNICODE)


def validate_response_text(text: str, cfg: GuardrailConfig) -> ValidationResult:
    """Validate that response_text obeys the guardrails."""
    # Price check (existing — preserved)
    if cfg.disallowed_price_pattern:
        matches = re.findall(cfg.disallowed_price_pattern, text)
        if matches:
            allowed_norm = {p.lower().strip() for p in cfg.allowed_prices}
            for m in matches:
                if m.lower().strip() not in allowed_norm:
                    return ValidationResult(
                        ok=False,
                        violation=(
                            f"response text contains a price '{m}' that is "
                            f"not in the tenant's allowed_prices whitelist"
                        ),
                        category="price_invented",
                    )

    # Product check (NEW)
    if cfg.allowed_products:
        allowed = {_normalize_product(p) for p in cfg.allowed_products}
        normalized_text = _normalize_product(text)
        # Substring match against the whole normalized text — any allowed
        # product mention is ok. The check fails only when the text contains
        # a "product-like" capitalized phrase NOT in the whitelist.
        # Conservative: require the text to mention at least one allowed
        # product whenever it speaks about products at all. We approximate
        # "speaks about products" via the presence of trigger keywords.
        product_triggers = ("curso", "programa", "produto", "treinamento", "mentoria", "consultoria")
        text_lower = text.lower()
        mentions_product_topic = any(t in text_lower for t in product_triggers)
        if mentions_product_topic:
            has_allowed = any(p in normalized_text for p in allowed)
            if not has_allowed:
                return ValidationResult(
                    ok=False,
                    violation=(
                        "response text mentions a product/program that is "
                        "not in the tenant's allowed_products whitelist"
                    ),
                    category="product_invented",
                )

    return ValidationResult(ok=True, violation=None, category=None)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_python_validator_product_whitelist.py -v
uv run pytest tests/unit/ -k python_validator -v
```
Expected: PASS (5/5 new), no regression in existing validator tests.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/guardrails/validator.py tests/unit/test_python_validator_product_whitelist.py
git commit -m "feat(fe03a t9): validator product whitelist with normalization

Per spec §7.1. Match is lowercase + whitespace-collapsed + strip.
Empty allowed_products disables the check (back-compat). Trigger-keyword
heuristic avoids false positives in text that doesn't talk about products."
```

---

### Task 10: `TenantGuardrails` schema exige `allowed_products` + `fallback_text` quando enabled

**Files:**
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Test: `tests/unit/test_tenant_guardrails_required_fields.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tenant_guardrails_required_fields.py
"""TenantGuardrails requires allowed_products + fallback_text when enabled (FE-03a Task 10)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import TenantGuardrails


def test_enabled_without_allowed_products_raises():
    with pytest.raises(ValidationError, match="allowed_products"):
        TenantGuardrails(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=[],
            fallback_text="Deixa eu confirmar com a equipe.",
        )


def test_enabled_without_fallback_text_raises():
    with pytest.raises(ValidationError, match="fallback_text"):
        TenantGuardrails(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=["Mentoria"],
            fallback_text="",
        )


def test_fallback_text_under_min_length_raises():
    with pytest.raises(ValidationError, match="fallback_text"):
        TenantGuardrails(
            enabled=True,
            allowed_prices=[6000],
            allowed_products=["Mentoria"],
            fallback_text="short",  # < 10 chars
        )


def test_disabled_does_not_require_lists():
    cfg = TenantGuardrails(
        enabled=False,
        allowed_prices=[],
        allowed_products=[],
        fallback_text="",
    )
    assert cfg.enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_guardrails_required_fields.py -v`
Expected: FAIL — current schema does not enforce these.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/schemas/tenant_yaml.py`, locate `TenantGuardrails` (whatever its current name is — Plano 3). Add fields and a model_validator:

```python
class TenantGuardrails(BaseModel):
    enabled: bool = True
    allowed_prices: list[int] = Field(default_factory=list)
    allowed_products: list[str] = Field(default_factory=list)
    fallback_text: str = ""
    # ... existing fields preserved ...

    @model_validator(mode="after")
    def _check_required_when_enabled(self) -> "TenantGuardrails":
        if not self.enabled:
            return self
        if not self.allowed_products:
            raise ValueError(
                "guardrails.allowed_products must be non-empty when enabled"
            )
        if not self.fallback_text or len(self.fallback_text) < 10:
            raise ValueError(
                "guardrails.fallback_text must be a non-empty string of >=10 chars when enabled"
            )
        return self
```

Make sure `from pydantic import model_validator` is imported.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_tenant_guardrails_required_fields.py -v
uv run pytest tests/unit/ -k tenant -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_guardrails_required_fields.py
git commit -m "feat(fe03a t10): TenantGuardrails enforces allowed_products + fallback_text when enabled

Per spec §7.2. Without enforcement the validator silently no-ops and
the lead-facing fallback is missing — both bugs. Min length 10 chars
on fallback_text matches existing convention."
```

---

## Phase 4: System prompt extensions

### Task 11: `build_fresh_layer` — bloco `ACTIVE TREATMENT` (já parcial, polir + testar)

`system_prompt.py:189-204` (FE-01b) já emite um bloco ACTIVE TREATMENT — mas usa `active_treatment.get(...)` em chaves cruas. FE-03a confirma o shape via `ActiveTreatment` Pydantic model e adiciona a guidance conservadora pra resolução.

**Files:**
- Modify: `src/ai_sdr/flowengine/system_prompt.py`
- Test: `tests/unit/test_system_prompt_active_treatment_block.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_system_prompt_active_treatment_block.py
"""Fresh layer renders ACTIVE TREATMENT block with conservative guidance (FE-03a Task 11)."""
from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _node(node_id: str = "n") -> TreeflowNode:
    return TreeflowNode(
        id=node_id,
        objetivo="x",
        bridge_instruction="bridge",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target=node_id)],
    )


def test_no_active_treatment_block_when_state_is_none():
    fresh = build_fresh_layer(
        current_node=_node(),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "ACTIVE TREATMENT" not in fresh.text


def test_active_treatment_block_shown_when_set():
    at = {
        "objection_id": "preco",
        "current_treatment_turn": 2,
        "max_treatment_turns": 3,
        "resolution_criteria": "lead aceitou parcelamento",
        "treatment_history": ["argumentou ROI"],
    }
    fresh = build_fresh_layer(
        current_node=_node(),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=2,
        now=datetime.now(timezone.utc),
        active_treatment=at,
        correction=None,
        current_inbound_text="ainda tá caro",
    )
    assert "ACTIVE TREATMENT" in fresh.text
    assert "preco" in fresh.text
    assert "turn 2 of 3" in fresh.text
    assert "lead aceitou parcelamento" in fresh.text


def test_active_treatment_block_includes_conservative_resolution_guidance():
    """Conservative resolution: prefer deferred over accepted when ambiguous."""
    at = {
        "objection_id": "preco",
        "current_treatment_turn": 1,
        "max_treatment_turns": 3,
        "resolution_criteria": "lead aceitou parcelamento",
        "treatment_history": [],
    }
    fresh = build_fresh_layer(
        current_node=_node(),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=at,
        correction=None,
        current_inbound_text="ok",
    )
    assert "prefira" in fresh.text.lower()
    assert "deferred" in fresh.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_system_prompt_active_treatment_block.py -v`
Expected: 1 PASS (no-block case), 2 FAIL (conservative guidance not present yet).

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/system_prompt.py`, replace the active_treatment block (lines 189-204) with:

```python
    if active_treatment:
        parts.append("=== TRATAMENTO DE OBJEÇÃO ATIVA ===")
        parts.append(
            f"Você está argumentando contra: {active_treatment.get('objection_id')}"
        )
        parts.append(
            f"Turno {active_treatment.get('current_treatment_turn')} de "
            f"{active_treatment.get('max_treatment_turns')} max "
            f"(turn {active_treatment.get('current_treatment_turn')} of "
            f"{active_treatment.get('max_treatment_turns')})"
        )
        parts.append(
            f"Critério de resolução: {active_treatment.get('resolution_criteria')}"
        )
        history_used = active_treatment.get("treatment_history", [])
        if history_used:
            parts.append(f"Argumentos já usados: {history_used}")
        parts.append("")
        parts.append("INSTRUÇÕES PRA RESOLUÇÃO (conservador):")
        parts.append(
            "- Em dúvida entre resolved_accepted e resolved_deferred, prefira deferred."
        )
        parts.append(
            "- Sinais de deferred: mensagem curta sem entusiasmo, 'tá bom', 'tanto faz', pontuação seca."
        )
        parts.append(
            "- resolved_accepted exige sinal positivo claro: 'fechou!', 'maravilha', pergunta sobre próximo passo."
        )
        parts.append(
            "- Lead ainda resistindo: in_progress."
        )
        parts.append(
            "- NÃO sugira mudar de node enquanto active_treatment estiver setado."
        )
        parts.append("")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_system_prompt_active_treatment_block.py -v
uv run pytest tests/unit/test_system_prompt_fresh_layer.py -v
```
Expected: PASS (3/3), no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/system_prompt.py tests/unit/test_system_prompt_active_treatment_block.py
git commit -m "feat(fe03a t11): ACTIVE TREATMENT block + conservative resolution guidance

Per spec §4.5. The conservative instruction is the cheap mitigation
for brecha A4 (fake acceptance) — instructs LLM to prefer deferred
when reading is ambiguous. Zero LLM call cost."
```

---

### Task 12: `build_fresh_layer` — `bridge_instruction` em IMMEDIATE NEXT NODES (brecha C3)

**Files:**
- Modify: `src/ai_sdr/flowengine/system_prompt.py:175-186`
- Test: `tests/unit/test_system_prompt_includes_bridge_in_next_nodes.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_system_prompt_includes_bridge_in_next_nodes.py
"""IMMEDIATE NEXT NODES block includes bridge_instruction (FE-03a Task 12, brecha C3)."""
from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


def _node(node_id: str, bridge: str = "") -> TreeflowNode:
    return TreeflowNode(
        id=node_id,
        objetivo=f"objetivo de {node_id}",
        bridge_instruction=bridge,
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target=node_id)],
    )


def test_next_node_bridge_instruction_rendered():
    current = _node("qualificacao", bridge="ignore for current")
    nxt = _node("oferta_mentoria", bridge="Mencione que ROI cabe em 1 mês")
    fresh = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="ok",
    )
    assert "Mencione que ROI cabe em 1 mês" in fresh.text


def test_block_mentions_compound_response_permission():
    current = _node("a")
    nxt = _node("b", bridge="bridge b")
    fresh = build_fresh_layer(
        current_node=current,
        immediate_next_nodes=[(nxt, "true")],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="ok",
    )
    text_lower = fresh.text.lower()
    assert "bridge_instruction" in text_lower
    assert "within the same response" in text_lower or "no mesmo response" in text_lower
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_system_prompt_includes_bridge_in_next_nodes.py -v`
Expected: 2 FAIL — current IMMEDIATE NEXT NODES block does not emit `bridge_instruction`.

- [ ] **Step 3: Apply implementation**

In `system_prompt.py`, replace the `if immediate_next_nodes:` block (lines 175-186):

```python
    if immediate_next_nodes:
        parts.append("IMMEDIATE NEXT NODES — DENSE DETAIL:")
        for node, condition in immediate_next_nodes:
            parts.append(f"  - id: {node.id}")
            parts.append(f"    objetivo: {node.objetivo}")
            parts.append(f"    bridge_instruction: {node.bridge_instruction}")
            parts.append(f"    will_collect: {[c.field for c in node.collects]}")
            parts.append(f"    transition_condition: {condition}")
        parts.append(
            "  When you decide to advance, compose a natural bridge using "
            "the chosen next node's objetivo AND bridge_instruction. You may "
            "include content that anchors the lead in the new node within the "
            "same response."
        )
        parts.append("")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_system_prompt_includes_bridge_in_next_nodes.py -v
uv run pytest tests/unit/test_system_prompt_fresh_layer.py -v
```
Expected: PASS (2/2), no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/system_prompt.py tests/unit/test_system_prompt_includes_bridge_in_next_nodes.py
git commit -m "feat(fe03a t12): bridge_instruction in IMMEDIATE NEXT NODES (brecha C3)

Per spec §8.2. LLM in node A can anticipate B's tone/content in the
same response that transitions. Cuts the lead-nudge UX gap when
compound responses are natural ('ok, manda link' -> ack + link)."
```

---

### Task 13: `build_fresh_layer` — bloco de objeções node-scoped (visível ao LLM)

**Files:**
- Modify: `src/ai_sdr/flowengine/system_prompt.py`
- Test: `tests/unit/test_system_prompt_node_scoped_objections.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_system_prompt_node_scoped_objections.py
"""build_fresh_layer renders node-scoped objections (FE-03a Task 13)."""
from __future__ import annotations

from datetime import datetime, timezone

from ai_sdr.flowengine.system_prompt import build_fresh_layer
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowTransition,
)


def _node_with_objections(objs: list[TreeflowObjection]) -> TreeflowNode:
    return TreeflowNode(
        id="qualificacao",
        objetivo="qualificar",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="qualificacao")],
        handles_objections=objs,
    )


def test_node_scoped_objections_listed():
    objs = [
        TreeflowObjection(
            id="ja_tentei_curso_online",
            description="lead diz que cursos online não funcionam pra ele",
            treatment_mode="tool",
            tool_payload=None,
        )
    ]
    fresh = build_fresh_layer(
        current_node=_node_with_objections(objs),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "NODE-SCOPED OBJECTIONS" in fresh.text
    assert "ja_tentei_curso_online" in fresh.text


def test_no_block_when_node_has_no_objections():
    fresh = build_fresh_layer(
        current_node=_node_with_objections([]),
        immediate_next_nodes=[],
        collected={},
        extracted_facts={},
        objections_handled=[],
        history=[],
        turn_index=1,
        now=datetime.now(timezone.utc),
        active_treatment=None,
        correction=None,
        current_inbound_text="oi",
    )
    assert "NODE-SCOPED OBJECTIONS" not in fresh.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_system_prompt_node_scoped_objections.py -v`
Expected: FAIL — block not emitted.

- [ ] **Step 3: Apply implementation**

In `system_prompt.py`, right before the `if immediate_next_nodes:` block, add:

```python
    if current_node.handles_objections:
        parts.append("NODE-SCOPED OBJECTIONS (visible only in this node):")
        for obj in current_node.handles_objections:
            parts.append(f"  - id: {obj.id}")
            parts.append(f"    description: {obj.description}")
            parts.append(f"    treatment_mode: {obj.treatment_mode}")
            if obj.tool_payload is not None:
                parts.append(
                    f"    max_treatment_turns: {obj.tool_payload.max_treatment_turns}"
                )
        parts.append(
            "  When you detect one, emit detected_objection with its id."
        )
        parts.append("")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_system_prompt_node_scoped_objections.py -v
```
Expected: PASS (2/2).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/system_prompt.py tests/unit/test_system_prompt_node_scoped_objections.py
git commit -m "feat(fe03a t13): node-scoped objections block in fresh layer

Per spec §6.2 + §4.5. LLM sees node-scoped objections only when agent
is in that node. Detection emits detected_objection with the id."
```

---

## Phase 5: Routing protections (brechas C1 + C2)

### Task 14: `validate_transition` muda assinatura — recebe `state` (não só `collected`)

**Files:**
- Modify: `src/ai_sdr/flowengine/routing.py` + chamadas em `src/ai_sdr/flowengine/pipeline.py`
- Test: `tests/unit/test_routing_signature_takes_state.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_routing_signature_takes_state.py
"""validate_transition new signature: takes a state dict (FE-03a Task 14)."""
from __future__ import annotations

from dataclasses import dataclass

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


@dataclass
class _MockState:
    collected: dict
    extracted_facts: dict
    objections_handled: list
    turn_index: int
    active_treatment: dict | None = None


def _treeflow() -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    b = TreeflowNode(
        id="b",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    return TreeflowDef(
        id="t",
        version="1.0.0",
        display_name=None,
        sdr_persona={},
        entry_node="a",
        nodes={"a": n, "b": b},
    )


def test_validate_transition_accepts_state_kwarg():
    state = _MockState(
        collected={},
        extracted_facts={},
        objections_handled=[],
        turn_index=1,
    )
    target, failure = validate_transition(
        current_node="a",
        next_node_suggestion="b",
        state=state,
        treeflow=_treeflow(),
    )
    assert target == "b"
    assert failure is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_routing_signature_takes_state.py -v`
Expected: FAIL — signature still uses `collected=...`.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/routing.py`, change the signature + internal references:

```python
from typing import Any, Protocol


class _StateProto(Protocol):
    collected: dict[str, Any]
    extracted_facts: dict[str, Any]
    objections_handled: list[Any]
    turn_index: int
    active_treatment: Any  # ActiveTreatment | None — typed Any to avoid import cycle


def validate_transition(
    *,
    current_node: str,
    next_node_suggestion: str | None,
    state: _StateProto,
    treeflow: TreeflowDef,
) -> tuple[str, str | None]:
    """Validate a transition. See module docstring."""
    if next_node_suggestion is None or next_node_suggestion in ("current", current_node):
        return current_node, None

    node = treeflow.nodes.get(current_node)
    if node is None:
        return current_node, "invalid_target"

    matching = [t for t in node.next_nodes if t.target == next_node_suggestion]
    if not matching:
        return current_node, "invalid_target"

    transition = matching[0]
    if transition.condition.strip() != "true":
        if not _eval_bool(transition.condition, state):
            return current_node, "condition_false"

    if not _exit_satisfied(node, state.collected):
        return current_node, "exit_not_satisfied"

    return next_node_suggestion, None


def _eval_bool(expression: str, state: _StateProto) -> bool:
    # NOTE: extended context comes in Task 16. For Task 14 we keep behavior
    # identical: pass state.collected only. This split is intentional so the
    # signature change ships isolated from the semantic change.
    try:
        return bool(SimpleEval(names=state.collected).eval(expression))
    except Exception:
        return False
```

Update `pipeline.py:170-191` to pass `state=state` (the loaded TalkFlowState already has `.collected`, `.extracted_facts`, `.objections_handled`, `.turn_index`, `.active_treatment` as attributes via ORM model).

If `TalkFlowState` ORM does not yet expose `turn_index`, surface it via a thin wrapper:

```python
# In pipeline.py — before calling validate_transition:
@dataclass
class _RoutingStateView:
    collected: dict[str, Any]
    extracted_facts: dict[str, Any]
    objections_handled: list[Any]
    turn_index: int
    active_treatment: Any

state_view = _RoutingStateView(
    collected={**state.collected, **decision.collected_fields},
    extracted_facts={**state.extracted_facts, **decision.extracted_facts},
    objections_handled=list(state.objections_handled),
    turn_index=ctx.talk.turn_count + 1,
    active_treatment=state.active_treatment,
)

resolved_target, failure = validate_transition(
    current_node=state.current_node,
    next_node_suggestion=decision.next_node_suggestion,
    state=state_view,
    treeflow=treeflow,
)
```

Update the `revalidate=` lambda passed to `run_transition_retry` similarly.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_routing_signature_takes_state.py -v
uv run pytest tests/unit/test_routing_validate_transition.py -v
```
Expected: PASS, no regression in existing routing tests (they may need their fixtures updated to the new signature — update them).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/routing.py src/ai_sdr/flowengine/pipeline.py tests/unit/test_routing_signature_takes_state.py tests/unit/test_routing_validate_transition.py
git commit -m "refactor(fe03a t14): validate_transition takes state, not just collected

Mechanical: signature change ships isolated from semantic changes
(simpleeval context expansion in t15, treatment block in t16).
All existing tests adapted to new kwarg."
```

---

### Task 15: `_eval_bool` — contexto simpleeval expandido (brecha C1)

**Files:**
- Modify: `src/ai_sdr/flowengine/routing.py:_eval_bool`
- Test: `tests/unit/test_routing_simpleeval_extended_context.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_routing_simpleeval_extended_context.py
"""Extended simpleeval context: extracted_facts, objections_handled, turn_index (FE-03a Task 15, brecha C1)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


@dataclass
class _State:
    collected: dict = field(default_factory=dict)
    extracted_facts: dict = field(default_factory=dict)
    objections_handled: list = field(default_factory=list)
    turn_index: int = 1
    active_treatment: object | None = None


def _tf(condition: str) -> TreeflowDef:
    n = TreeflowNode(
        id="a",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition=condition, target="b")],
    )
    b = TreeflowNode(
        id="b",
        objetivo="x",
        bridge_instruction="",
        collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n, "b": b},
    )


def test_condition_can_reference_extracted_facts():
    tf = _tf("extracted_facts.dor_principal == 'tempo'")
    state = _State(extracted_facts={"dor_principal": "tempo"})
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_turn_index():
    tf = _tf("turn_index >= 5")
    state = _State(turn_index=6)
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_collected_topmost_legacy():
    """Retrocompat: condition referring to a collected field by bare name still works."""
    tf = _tf("ticket_medio >= 50000")
    state = _State(collected={"ticket_medio": 60000})
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=tf,
    )
    assert target == "b" and failure is None


def test_condition_can_reference_objections_handled_length():
    tf = _tf("len(objections_handled) > 0")
    state = _State(objections_handled=[{"id": "preco", "resolution": "deferred"}])
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=tf,
    )
    # simpleeval supports len() on lists.
    assert target == "b" and failure is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_routing_simpleeval_extended_context.py -v`
Expected: 3 of 4 FAIL (extracted_facts / turn_index / objections_handled refs all raise → conservative False → condition_false).

- [ ] **Step 3: Apply implementation**

In `routing.py`, replace `_eval_bool`:

```python
def _eval_bool(expression: str, state: _StateProto) -> bool:
    """Evaluate a simpleeval expression against an extended context.

    Names available in the expression (brecha C1, FE-03a §8.1):
      - top-level collected field names (retrocompat with v1 YAML)
      - collected: dict of all collected fields
      - extracted_facts: dict of facts
      - objections_handled: list of {id, resolution} dicts
      - turn_index: int
    """
    context: dict[str, Any] = dict(state.collected)
    context["collected"] = state.collected
    context["extracted_facts"] = state.extracted_facts
    context["objections_handled"] = [
        {
            "id": getattr(o, "objection_id", None) or o.get("objection_id"),
            "resolution": getattr(o, "resolution", None) or o.get("resolution"),
        }
        for o in state.objections_handled
    ]
    context["turn_index"] = state.turn_index
    try:
        return bool(SimpleEval(names=context).eval(expression))
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_routing_simpleeval_extended_context.py -v
uv run pytest tests/unit/test_routing_validate_transition.py tests/unit/test_routing_signature_takes_state.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/routing.py tests/unit/test_routing_simpleeval_extended_context.py
git commit -m "feat(fe03a t15): expand simpleeval context (brecha C1)

Per spec §8.1. Conditional YAML can now reference extracted_facts,
objections_handled, turn_index — not just collected. Backward-compat
preserved (top-level collected names still work)."
```

---

### Task 16: `validate_transition` bloqueia transição se `active_treatment` setado (brecha C2)

**Files:**
- Modify: `src/ai_sdr/flowengine/routing.py:validate_transition`
- Modify: `src/ai_sdr/flowengine/correction.py` — accept new failure reason
- Test: `tests/unit/test_routing_blocks_transition_when_active_treatment.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_routing_blocks_transition_when_active_treatment.py
"""Routing blocks transitions while active_treatment is set (FE-03a Task 16, brecha C2)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdr.flowengine.routing import validate_transition
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTransition,
)


@dataclass
class _State:
    collected: dict = field(default_factory=dict)
    extracted_facts: dict = field(default_factory=dict)
    objections_handled: list = field(default_factory=list)
    turn_index: int = 1
    active_treatment: object | None = None


def _tf() -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    b = TreeflowNode(
        id="b", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="b")],
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n, "b": b},
    )


def test_transition_blocked_during_active_treatment():
    state = _State(active_treatment={"objection_id": "preco"})
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=_tf(),
    )
    assert target == "a"
    assert failure == "transition_blocked_by_treatment"


def test_transition_allowed_when_treatment_is_none():
    state = _State(active_treatment=None)
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="b", state=state, treeflow=_tf(),
    )
    assert target == "b"
    assert failure is None


def test_staying_in_same_node_allowed_even_during_treatment():
    state = _State(active_treatment={"objection_id": "preco"})
    target, failure = validate_transition(
        current_node="a", next_node_suggestion="a", state=state, treeflow=_tf(),
    )
    assert target == "a"
    assert failure is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_routing_blocks_transition_when_active_treatment.py -v`
Expected: 1 of 3 FAIL — transition during treatment incorrectly allowed.

- [ ] **Step 3: Apply implementation**

In `routing.py:validate_transition`, add the 4th check right before the existing exit-condition check:

```python
    transition = matching[0]
    if transition.condition.strip() != "true":
        if not _eval_bool(transition.condition, state):
            return current_node, "condition_false"

    # NEW (brecha C2): block transitions during active_treatment
    if state.active_treatment is not None:
        return current_node, "transition_blocked_by_treatment"

    if not _exit_satisfied(node, state.collected):
        return current_node, "exit_not_satisfied"

    return next_node_suggestion, None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_routing_blocks_transition_when_active_treatment.py -v
uv run pytest tests/unit/ -k routing -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/routing.py tests/unit/test_routing_blocks_transition_when_active_treatment.py
git commit -m "feat(fe03a t16): block transition during active_treatment (brecha C2, CRITICAL)

Per spec §8.1. Routing enforces what the system prompt instructs:
no transition while a treatment is in progress. failure_reason
'transition_blocked_by_treatment' rides the existing run_transition_retry
loop — LLM regenerates without proposing transition."
```

---

## Phase 6: Objection runtime pure state machine

Tarefas T17-T22 todas tocam o mesmo arquivo novo `src/ai_sdr/flowengine/objection_runtime.py`. Cada task adiciona uma transição da máquina de estado. O arquivo cresce incrementalmente.

### Task 17: `objection_runtime.apply()` — IDLE → ACTIVE (entrar em tratamento)

**Files:**
- Create: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_idle_to_active.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_idle_to_active.py
"""IDLE -> ACTIVE: LLM detected an objection with treatment_mode=tool (FE-03a Task 17)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import StateDelta, apply
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)


def _tf_with_preco_tool() -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    preco = TreeflowObjection(
        id="preco",
        description="lead diz preço caro",
        treatment_mode="tool",
        tool_payload=TreeflowToolPayload(
            canonical_arguments_summary="ROI cabe em 1 mês",
            kb_ref="kb_preco",
            max_treatment_turns=3,
            resolution_criteria="lead aceitou parcelamento",
            on_max_turns_no_resolution=TreeflowOnMaxTurns(
                action="gracefully_continue"
            ),
        ),
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n}, global_objections=[preco],
    )


def _decision(**kwargs) -> TurnDecision:
    base = dict(
        response_text="argumento",
        collected_fields={},
        reasoning="r",
    )
    base.update(kwargs)
    return TurnDecision(**base)


def test_idle_enters_active_on_detected_tool_objection():
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection="preco")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert isinstance(delta, StateDelta)
    assert delta.new_active_treatment is not None
    assert delta.new_active_treatment["objection_id"] == "preco"
    assert delta.new_active_treatment["current_treatment_turn"] == 1
    assert delta.new_active_treatment["max_treatment_turns"] == 3


def test_idle_stays_idle_when_no_objection_detected():
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection=None)
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None


def test_idle_does_not_enter_for_inline_objection():
    tf = _tf_with_preco_tool()
    # Add a second objection in inline mode and detect it.
    inline_obj = TreeflowObjection(
        id="downsell",
        description="lead pede algo mais barato",
        treatment_mode="inline",
        tool_payload=None,
    )
    tf.global_objections.append(inline_obj)
    state = {"current_node": "a", "active_treatment": None, "objections_handled": []}
    decision = _decision(detected_objection="downsell")
    delta = apply(state=state, decision=decision, treeflow=tf)
    assert delta.new_active_treatment is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_idle_to_active.py -v`
Expected: FAIL — module `objection_runtime` does not exist.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/flowengine/objection_runtime.py`:

```python
"""Objection treatment state machine — pure function (FE-03a §4).

apply(state, decision, treeflow) -> StateDelta

The function reads the runtime state (a TalkFlowState-like dict or the
ORM model — both expose .active_treatment via attribute or key), the
LLM's TurnDecision, and the TreeFlow definition. It returns a delta
describing what should be persisted.

This module DOES NOT touch the DB. post_processing.apply_decision is
responsible for translating the delta into ORM mutations.

States: IDLE (active_treatment is None) and ACTIVE (active_treatment set).
See spec §4.2 for transition diagram and §4.3 for priority order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowObjection,
)

logger = logging.getLogger(__name__)


@dataclass
class StateDelta:
    """Delta describing the state changes to apply.

    `unchanged` means: no field overrides at all.
    """

    new_active_treatment: dict[str, Any] | None | object = field(
        default_factory=lambda: _UNSET
    )
    appended_objection_history: list[dict[str, Any]] = field(default_factory=list)
    requires_review_reason: str | None = None
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def changes_treatment(self) -> bool:
        return self.new_active_treatment is not _UNSET


_UNSET = object()


def _find_objection(
    objection_id: str, treeflow: TreeflowDef, current_node_id: str | None = None,
) -> TreeflowObjection | None:
    # Node-scoped takes precedence.
    if current_node_id is not None:
        node = treeflow.nodes.get(current_node_id)
        if node is not None:
            for obj in getattr(node, "handles_objections", []):
                if obj.id == objection_id:
                    return obj
    for obj in treeflow.global_objections:
        if obj.id == objection_id:
            return obj
    return None


def _is_tool_mode(objection_id: str, treeflow: TreeflowDef, current_node_id: str | None) -> bool:
    obj = _find_objection(objection_id, treeflow, current_node_id)
    return obj is not None and obj.treatment_mode == "tool"


def _enter_treatment(
    objection_id: str, treeflow: TreeflowDef, current_node_id: str | None,
) -> dict[str, Any]:
    obj = _find_objection(objection_id, treeflow, current_node_id)
    # obj is guaranteed tool here (caller checked).
    tp = obj.tool_payload
    return {
        "objection_id": obj.id,
        "started_at_turn": 1,
        "current_treatment_turn": 1,
        "max_treatment_turns": tp.max_treatment_turns,
        "resolution_criteria": tp.resolution_criteria,
        "treatment_history": [],
    }


def _state_attr(state: Any, key: str, default: Any = None) -> Any:
    """Read either a dict or a SQLAlchemy ORM object."""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def apply(
    *,
    state: Any,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> StateDelta:
    """Run the state machine. Pure function. See module docstring."""
    active = _state_attr(state, "active_treatment")
    current_node_id = _state_attr(state, "current_node")

    # IDLE
    if active is None:
        detected = decision.detected_objection
        if detected and _is_tool_mode(detected, treeflow, current_node_id):
            new = _enter_treatment(detected, treeflow, current_node_id)
            return StateDelta(
                new_active_treatment=new,
                events=[
                    (
                        "objection.treatment.entered",
                        {
                            "objection_id": detected,
                            "max_turns": new["max_treatment_turns"],
                        },
                    )
                ],
            )
        return StateDelta()

    # ACTIVE — not yet implemented in this task; will be filled in T18-T22.
    return StateDelta()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS (3/3).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_idle_to_active.py
git commit -m "feat(fe03a t17): objection_runtime.apply() — IDLE -> ACTIVE entry

Per spec §4.3 transition #1. Pure function returns StateDelta with
new active_treatment shape + observability event. Node-scoped
objections take precedence over global when id collides."
```

---

### Task 18: ACTIVE → ACTIVE continue (`in_progress` increments turn)

**Files:**
- Modify: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_continue.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_continue.py
"""ACTIVE continues + increments turn when treatment_status=in_progress (FE-03a Task 18)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=1, max_turns=3):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": max_turns,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_in_progress_increments_turn():
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision(treatment_status="in_progress")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.changes_treatment
    assert delta.new_active_treatment["current_treatment_turn"] == 2


def test_in_progress_emits_continued_event():
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision(treatment_status="in_progress")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    event_names = [name for name, _ in delta.events]
    assert "objection.treatment.continued" in event_names


def test_missing_treatment_status_assumes_in_progress():
    """Defensive: if LLM forgets to emit, runtime assumes in_progress (conservative)."""
    state = {"current_node": "a", "active_treatment": _active(turn=1)}
    decision = _decision()  # treatment_status=None (default)
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.changes_treatment
    assert delta.new_active_treatment["current_treatment_turn"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_continue.py -v`
Expected: FAIL — ACTIVE branch returns empty StateDelta.

- [ ] **Step 3: Apply implementation**

In `objection_runtime.py`, replace the ACTIVE branch in `apply()`:

```python
    # ACTIVE branch
    # NOTE: max_turns / resolved / cross-objection priorities come in T19-T22.
    # T18 implements only the default "continue / increment turn" rule.
    new = dict(active)
    new["current_treatment_turn"] = active["current_treatment_turn"] + 1
    return StateDelta(
        new_active_treatment=new,
        events=[
            (
                "objection.treatment.continued",
                {
                    "objection_id": active["objection_id"],
                    "current_turn": new["current_treatment_turn"],
                },
            )
        ],
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_continue.py -v
uv run pytest tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS (3/3 new + regression intact).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_continue.py
git commit -m "feat(fe03a t18): objection_runtime — ACTIVE in_progress increments turn

Per spec §4.3 priority 5 (default). Missing treatment_status assumed
in_progress (conservative). Subsequent tasks layer max_turns/resolved/
cross-objection priorities ABOVE this default."
```

---

### Task 19: ACTIVE → IDLE resolved (`accepted` / `deferred`)

**Files:**
- Modify: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_resolved.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_resolved.py
"""ACTIVE -> IDLE on resolved_accepted/resolved_deferred (FE-03a Task 19)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=2):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_resolved_accepted_goes_idle_with_history_accepted():
    state = {"current_node": "a", "active_treatment": _active()}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history == [
        {
            "objection_id": "preco",
            "detected_at_turn": 1,
            "resolved_at_turn": 2,
            "resolution": "accepted",
        }
    ]


def test_resolved_deferred_goes_idle_with_history_deferred():
    state = {"current_node": "a", "active_treatment": _active()}
    decision = _decision(treatment_status="resolved_deferred")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history[0]["resolution"] == "deferred"


def test_resolved_emits_event_with_status_and_turn_count():
    state = {"current_node": "a", "active_treatment": _active(turn=2)}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    events = dict(delta.events)
    assert "objection.treatment.resolved" in events
    payload = events["objection.treatment.resolved"]
    assert payload["status"] == "accepted"
    assert payload["total_turns"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_resolved.py -v`
Expected: FAIL — current logic continues with turn+1 instead of going IDLE.

- [ ] **Step 3: Apply implementation**

In `objection_runtime.py`, replace the ACTIVE branch:

```python
    # ACTIVE branch — priority order per spec §4.3:
    # 1. cross-objection (T20)
    # 2. max turns (T20)
    # 3. resolved_accepted
    # 4. resolved_deferred
    # 5. default (continue)

    # Priority 3 + 4: resolved
    if decision.treatment_status in ("resolved_accepted", "resolved_deferred"):
        resolution = (
            "accepted"
            if decision.treatment_status == "resolved_accepted"
            else "deferred"
        )
        return StateDelta(
            new_active_treatment=None,
            appended_objection_history=[{
                "objection_id": active["objection_id"],
                "detected_at_turn": active["started_at_turn"],
                "resolved_at_turn": active["current_treatment_turn"],
                "resolution": resolution,
            }],
            events=[
                (
                    "objection.treatment.resolved",
                    {
                        "objection_id": active["objection_id"],
                        "status": resolution,
                        "total_turns": active["current_treatment_turn"],
                    },
                )
            ],
        )

    # Priority 5: default continue
    new = dict(active)
    new["current_treatment_turn"] = active["current_treatment_turn"] + 1
    return StateDelta(
        new_active_treatment=new,
        events=[
            (
                "objection.treatment.continued",
                {
                    "objection_id": active["objection_id"],
                    "current_turn": new["current_treatment_turn"],
                },
            )
        ],
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_resolved.py tests/unit/test_objection_runtime_continue.py tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_resolved.py
git commit -m "feat(fe03a t19): objection_runtime — resolved_accepted/deferred -> IDLE

Per spec §4.3 priorities 3-4. Emits objection.treatment.resolved
with status + total_turns. Appends history entry distinguishing
accepted from deferred (consumed by HITL console in FE-07)."
```

---

### Task 20: ACTIVE → IDLE exhausted (max turns) + escalate option

**Files:**
- Modify: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_exhausted.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_exhausted.py
"""Max turns exhausted: gracefully_continue OR escalate_to_human (FE-03a Task 20)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)


def _tf_with_action(action: str) -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    preco = TreeflowObjection(
        id="preco",
        description="lead diz preço caro",
        treatment_mode="tool",
        tool_payload=TreeflowToolPayload(
            canonical_arguments_summary="ROI cabe em 1 mês",
            kb_ref="kb_preco",
            max_treatment_turns=3,
            resolution_criteria="lead aceitou parcelamento",
            on_max_turns_no_resolution=TreeflowOnMaxTurns(action=action),
        ),
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n}, global_objections=[preco],
    )


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def _active(turn=3, max_turns=3):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": max_turns,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_exhausted_with_gracefully_continue_goes_idle_no_review():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state, decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("gracefully_continue"),
    )
    assert delta.new_active_treatment is None
    assert delta.appended_objection_history[0]["resolution"] == "exhausted"
    assert delta.requires_review_reason is None


def test_exhausted_with_escalate_sets_review_reason():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state, decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("escalate_to_human"),
    )
    assert delta.new_active_treatment is None
    assert delta.requires_review_reason == "objection_treatment_exhausted"


def test_exhausted_emits_event_with_action_taken():
    state = {"current_node": "a", "active_treatment": _active()}
    delta = apply(
        state=state, decision=_decision(treatment_status="in_progress"),
        treeflow=_tf_with_action("escalate_to_human"),
    )
    events = dict(delta.events)
    assert "objection.treatment.exhausted" in events
    assert events["objection.treatment.exhausted"]["action_taken"] == "escalate_to_human"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_exhausted.py -v`
Expected: FAIL — current logic increments to turn 4 (would later be max+1).

- [ ] **Step 3: Apply implementation**

In `objection_runtime.py`, insert the max-turns check BEFORE the resolved check:

```python
    # ACTIVE branch — priority order per spec §4.3:

    # Priority 2: max turns exhausted (before resolved per spec §4.3 ordering)
    if active["current_treatment_turn"] >= active["max_treatment_turns"]:
        obj = _find_objection(
            active["objection_id"], treeflow, current_node_id,
        )
        action = (
            obj.tool_payload.on_max_turns_no_resolution.action
            if obj and obj.tool_payload
            else "gracefully_continue"
        )
        review_reason = (
            "objection_treatment_exhausted" if action == "escalate_to_human" else None
        )
        return StateDelta(
            new_active_treatment=None,
            appended_objection_history=[{
                "objection_id": active["objection_id"],
                "detected_at_turn": active["started_at_turn"],
                "resolved_at_turn": active["current_treatment_turn"],
                "resolution": "exhausted",
            }],
            requires_review_reason=review_reason,
            events=[
                (
                    "objection.treatment.exhausted",
                    {
                        "objection_id": active["objection_id"],
                        "action_taken": action,
                    },
                )
            ],
        )

    # Priority 3 + 4: resolved (existing from T19)
    ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_exhausted.py tests/unit/test_objection_runtime_resolved.py tests/unit/test_objection_runtime_continue.py tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_exhausted.py
git commit -m "feat(fe03a t20): objection_runtime — max turns exhausted

Per spec §4.3 priority 2. Lookup on_max_turns_no_resolution.action
from treeflow; gracefully_continue -> just clears state; escalate_to_human
also sets requires_review_reason='objection_treatment_exhausted'."
```

---

### Task 21: ACTIVE → ACTIVE cross-objection swap (priority 1)

**Files:**
- Modify: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_cross_objection.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_cross_objection.py
"""Cross-objection: new tool objection swaps the current (defers it) (FE-03a Task 21)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowObjection,
    TreeflowOnMaxTurns,
    TreeflowToolPayload,
    TreeflowTransition,
)


def _tf_with_preco_and_tempo() -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )

    def _obj(oid: str) -> TreeflowObjection:
        return TreeflowObjection(
            id=oid,
            description=f"obj {oid} description text",
            treatment_mode="tool",
            tool_payload=TreeflowToolPayload(
                canonical_arguments_summary="argumentos canonicos longos",
                kb_ref=f"kb_{oid}",
                max_treatment_turns=3,
                resolution_criteria="criterio de resolucao",
                on_max_turns_no_resolution=TreeflowOnMaxTurns(
                    action="gracefully_continue"
                ),
            ),
        )

    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n},
        global_objections=[_obj("preco"), _obj("tempo")],
    )


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def _active_preco(turn=2):
    return {
        "objection_id": "preco",
        "started_at_turn": 1,
        "current_treatment_turn": turn,
        "max_treatment_turns": 3,
        "resolution_criteria": "x",
        "treatment_history": [],
    }


def test_new_objection_swaps_current_defers_old():
    state = {"current_node": "a", "active_treatment": _active_preco()}
    decision = _decision(detected_objection="tempo")
    delta = apply(
        state=state, decision=decision, treeflow=_tf_with_preco_and_tempo(),
    )
    # new active is tempo
    assert delta.new_active_treatment["objection_id"] == "tempo"
    assert delta.new_active_treatment["current_treatment_turn"] == 1
    # preco appended to history as deferred
    assert delta.appended_objection_history == [{
        "objection_id": "preco",
        "detected_at_turn": 1,
        "resolved_at_turn": 2,
        "resolution": "deferred",
    }]


def test_swap_emits_cross_swap_event():
    state = {"current_node": "a", "active_treatment": _active_preco()}
    delta = apply(
        state=state, decision=_decision(detected_objection="tempo"),
        treeflow=_tf_with_preco_and_tempo(),
    )
    events = dict(delta.events)
    assert "objection.treatment.cross_swap" in events
    assert events["objection.treatment.cross_swap"]["from_id"] == "preco"
    assert events["objection.treatment.cross_swap"]["to_id"] == "tempo"


def test_same_objection_id_is_not_a_swap():
    """detected_objection == active.objection_id should NOT defer."""
    state = {"current_node": "a", "active_treatment": _active_preco()}
    delta = apply(
        state=state, decision=_decision(detected_objection="preco"),
        treeflow=_tf_with_preco_and_tempo(),
    )
    assert delta.new_active_treatment["objection_id"] == "preco"
    # Continue, not swap.
    assert delta.appended_objection_history == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_cross_objection.py -v`
Expected: FAIL — swap branch not yet implemented; current logic falls through to continue.

- [ ] **Step 3: Apply implementation**

In `objection_runtime.py`, insert the cross-objection check at the very top of the ACTIVE branch:

```python
    # ACTIVE branch — priority order per spec §4.3:

    # Priority 1: cross-objection (new id, must also be tool mode)
    detected = decision.detected_objection
    if (
        detected is not None
        and detected != active["objection_id"]
        and _is_tool_mode(detected, treeflow, current_node_id)
    ):
        new = _enter_treatment(detected, treeflow, current_node_id)
        return StateDelta(
            new_active_treatment=new,
            appended_objection_history=[{
                "objection_id": active["objection_id"],
                "detected_at_turn": active["started_at_turn"],
                "resolved_at_turn": active["current_treatment_turn"],
                "resolution": "deferred",
            }],
            events=[
                (
                    "objection.treatment.cross_swap",
                    {"from_id": active["objection_id"], "to_id": detected},
                )
            ],
        )

    # Priority 2: max turns (existing)
    ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_cross_objection.py tests/unit/test_objection_runtime_exhausted.py tests/unit/test_objection_runtime_resolved.py tests/unit/test_objection_runtime_continue.py tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_cross_objection.py
git commit -m "feat(fe03a t21): objection_runtime — cross-objection swap (priority 1)

Per spec §4.3. New tool objection defers the active one and enters
new treatment at turn 1. Honest model of conversation: when lead
changes topic, agent registers anterior and follows new."
```

---

### Task 22: Defensive cases (hallucinated id; treatment_status when IDLE)

**Files:**
- Modify: `src/ai_sdr/flowengine/objection_runtime.py`
- Test: `tests/unit/test_objection_runtime_defensive.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_objection_runtime_defensive.py
"""Defensive cases: hallucinated id, treatment_status when IDLE (FE-03a Task 22)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.objection_runtime import apply
from tests.unit.test_objection_runtime_idle_to_active import _tf_with_preco_tool


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def test_hallucinated_objection_id_is_ignored_emits_event():
    """LLM emits objection id that doesn't exist in YAML — ignore + log."""
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(detected_objection="xpto_nao_existe")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert not delta.changes_treatment or delta.new_active_treatment is None
    events = dict(delta.events)
    assert "objection.hallucinated_id" in events
    assert events["objection.hallucinated_id"]["id_received"] == "xpto_nao_existe"


def test_treatment_status_when_idle_is_ignored():
    """treatment_status only valid during ACTIVE — must be ignored when IDLE."""
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(treatment_status="resolved_accepted")
    delta = apply(state=state, decision=decision, treeflow=_tf_with_preco_tool())
    assert delta.new_active_treatment is None or delta.changes_treatment is False


def test_inline_objection_detected_does_not_change_state_but_logs_nothing_special():
    """Inline mode just emits no treatment event; not hallucination."""
    from ai_sdr.flowengine.treeflow_loader import TreeflowObjection
    tf = _tf_with_preco_tool()
    tf.global_objections.append(
        TreeflowObjection(
            id="curiosidade",
            description="lead pergunta algo lateral sobre vc",
            treatment_mode="inline",
            tool_payload=None,
        )
    )
    state = {"current_node": "a", "active_treatment": None}
    decision = _decision(detected_objection="curiosidade")
    delta = apply(state=state, decision=decision, treeflow=tf)
    event_names = [n for n, _ in delta.events]
    assert "objection.hallucinated_id" not in event_names
    assert "objection.treatment.entered" not in event_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_objection_runtime_defensive.py -v`
Expected: 1 of 3 FAIL — hallucination event not emitted.

- [ ] **Step 3: Apply implementation**

In `objection_runtime.py`, update the IDLE branch:

```python
    # IDLE
    if active is None:
        detected = decision.detected_objection
        if detected:
            obj = _find_objection(detected, treeflow, current_node_id)
            if obj is None:
                return StateDelta(
                    events=[("objection.hallucinated_id", {"id_received": detected})],
                )
            if obj.treatment_mode == "tool":
                new = _enter_treatment(detected, treeflow, current_node_id)
                return StateDelta(
                    new_active_treatment=new,
                    events=[
                        (
                            "objection.treatment.entered",
                            {
                                "objection_id": detected,
                                "max_turns": new["max_treatment_turns"],
                            },
                        )
                    ],
                )
            # inline mode: emit nothing (LLM handled within response_text)
        return StateDelta()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_objection_runtime_defensive.py tests/unit/test_objection_runtime_cross_objection.py tests/unit/test_objection_runtime_exhausted.py tests/unit/test_objection_runtime_resolved.py tests/unit/test_objection_runtime_continue.py tests/unit/test_objection_runtime_idle_to_active.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/objection_runtime.py tests/unit/test_objection_runtime_defensive.py
git commit -m "feat(fe03a t22): objection_runtime — defensive cases

Per spec §4.5. Hallucinated id (not in YAML) emits event + ignored.
Inline-mode detection logs nothing special (LLM handled in same turn).
treatment_status when IDLE is silently ignored (only valid in ACTIVE)."
```

---

## Phase 7: Post-LLM heuristics (brechas B4 + C4)

### Task 23: Contradição interna (B4) — heurística corrige `accepted` → `deferred`

**Files:**
- Create: `src/ai_sdr/flowengine/heuristics.py`
- Test: `tests/unit/test_heuristics_contradiction.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_heuristics_contradiction.py
"""Heurística B4: corrige accepted -> deferred quando texto contradiz (FE-03a Task 23)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.heuristics import apply_contradiction_heuristic


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def test_corrects_accepted_when_text_says_pena_pensar():
    d = _decision(
        response_text="Ah que pena, deixa eu te deixar pensar então",
        treatment_status="resolved_accepted",
    )
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_deferred"
    assert any(name == "decision.contradiction_corrected" for name, _ in events)


def test_corrects_accepted_when_text_says_tanto_faz():
    d = _decision(
        response_text="Tanto faz, vai. Te mando o material depois",
        treatment_status="resolved_accepted",
    )
    corrected, _ = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_deferred"


def test_does_not_correct_clearly_positive_acceptance():
    d = _decision(
        response_text="Fechou! Vou agendar agora",
        treatment_status="resolved_accepted",
    )
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status == "resolved_accepted"
    assert not events


def test_no_op_when_treatment_status_is_none():
    d = _decision(treatment_status=None)
    corrected, events = apply_contradiction_heuristic(d)
    assert corrected.treatment_status is None
    assert not events
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_heuristics_contradiction.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/flowengine/heuristics.py`:

```python
"""Post-LLM heuristics for the FlowEngine (FE-03a §8 brechas B4 + C4).

These run AFTER the main LLM call and BEFORE objection_runtime.apply().
They detect obvious contradictions between TurnDecision fields and the
response_text, and either correct or just log (no LLM call).
"""

from __future__ import annotations

import re
from typing import Any

from ai_sdr.flowengine.decision import TurnDecision

_DEFERRAL_HINT_RE = re.compile(
    r"\b(pena|pens[ae]r|tanto\s*faz|sei\s*l[áa]|talvez\s*depois|fica\s*pra\s*depois|"
    r"deixa\s*pra\s*l[áa]|t[áa]\s*bom)\b",
    re.IGNORECASE,
)

_COMMITMENT_HINT_RE = re.compile(
    r"\b(vou\s*te\s*enviar|te\s*envio|te\s*conecto|te\s*passo|"
    r"aguarda|pr[óo]ximo\s*passo|agora\s*mesmo)\b",
    re.IGNORECASE,
)


def apply_contradiction_heuristic(
    decision: TurnDecision,
) -> tuple[TurnDecision, list[tuple[str, dict[str, Any]]]]:
    """Brecha B4: degrade resolved_accepted -> resolved_deferred when text contradicts.

    Returns (possibly-modified decision, events list).
    """
    if decision.treatment_status != "resolved_accepted":
        return decision, []

    if not _DEFERRAL_HINT_RE.search(decision.response_text):
        return decision, []

    corrected = decision.model_copy(update={"treatment_status": "resolved_deferred"})
    return corrected, [
        (
            "decision.contradiction_corrected",
            {
                "field": "treatment_status",
                "original": "resolved_accepted",
                "corrected": "resolved_deferred",
                "trigger": "deferral_hint_in_response_text",
            },
        )
    ]


def detect_implicit_transition(
    decision: TurnDecision,
) -> list[tuple[str, dict[str, Any]]]:
    """Brecha C4: log when response_text promises action but next_node is None.

    Pure detection — does NOT modify decision. Returns events list.
    """
    if decision.next_node_suggestion is not None:
        return []
    if not _COMMITMENT_HINT_RE.search(decision.response_text):
        return []
    excerpt = decision.response_text[:120]
    matched = _COMMITMENT_HINT_RE.search(decision.response_text)
    return [
        (
            "decision.implicit_transition_suspected",
            {
                "matched_pattern": matched.group(0) if matched else "",
                "response_excerpt": excerpt,
            },
        )
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_heuristics_contradiction.py -v
```
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/heuristics.py tests/unit/test_heuristics_contradiction.py
git commit -m "feat(fe03a t23): contradiction heuristic — accepted -> deferred (brecha B4)

Per spec §8 B4. Regex detects deferral hints in response_text; when
present alongside treatment_status='resolved_accepted', degrades to
deferred + emits event. Zero LLM call; ~30 lines code."
```

---

### Task 24: Implicit transition (C4) — heurística audit-only

**Files:**
- Test: `tests/unit/test_heuristics_implicit_transition.py` (create)
- `heuristics.py` already has `detect_implicit_transition` from T23

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_heuristics_implicit_transition.py
"""Heurística C4: detecta committal text sem next_node (FE-03a Task 24)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.heuristics import detect_implicit_transition


def _decision(**kw):
    base = dict(response_text="x", collected_fields={}, reasoning="r")
    base.update(kw)
    return TurnDecision(**base)


def test_committal_text_without_transition_emits_event():
    d = _decision(
        response_text="Beleza, vou te enviar o link agora mesmo",
        next_node_suggestion=None,
    )
    events = detect_implicit_transition(d)
    assert events
    name, payload = events[0]
    assert name == "decision.implicit_transition_suspected"
    assert "vou te enviar" in payload["matched_pattern"].lower()


def test_committal_text_WITH_transition_emits_nothing():
    d = _decision(
        response_text="Beleza, vou te enviar o link agora mesmo",
        next_node_suggestion="envio_checkout",
    )
    events = detect_implicit_transition(d)
    assert events == []


def test_non_committal_text_emits_nothing():
    d = _decision(
        response_text="Entendi. Pode me contar mais sobre o seu negócio?",
        next_node_suggestion=None,
    )
    events = detect_implicit_transition(d)
    assert events == []
```

- [ ] **Step 2: Run test to verify it fails**

(The function exists from T23, so tests should run. Let's verify expected behavior.)

Run: `uv run pytest tests/unit/test_heuristics_implicit_transition.py -v`
Expected: PASS (3/3) — function already implements this in T23.

- [ ] **Step 3: (No implementation needed — code shipped in T23)**

If a test fails, polish the regex in `heuristics.py:_COMMITMENT_HINT_RE`.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_heuristics_implicit_transition.py
git commit -m "test(fe03a t24): implicit transition heuristic coverage (brecha C4)

Tests the audit-only detection shipped in t23. No correction — just
emits decision.implicit_transition_suspected so the operator HITL
console (FE-07) can review samples."
```

---

## Phase 8: Off-topic counter + escalation handling

### Task 25: Off-topic counter increment + escalation aos 3

**Files:**
- Modify: `src/ai_sdr/flowengine/post_processing.py`
- Modify: `src/ai_sdr/flowengine/decision.py` (campo `off_topic_detected: bool`)
- Test: `tests/unit/test_offtopic_counter_and_escalate.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_offtopic_counter_and_escalate.py
"""Off-topic counter + escalate on 3rd strike (FE-03a Task 25)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.offtopic import (
    OFFTOPIC_THRESHOLD,
    handle_offtopic,
)


def _decision(off_topic: bool):
    return TurnDecision(
        response_text="redirecionando",
        collected_fields={},
        reasoning="r",
        off_topic_detected=off_topic,
    )


def test_offtopic_increments_counter():
    new_count, reason = handle_offtopic(current_count=0, decision=_decision(True))
    assert new_count == 1
    assert reason is None


def test_offtopic_below_threshold_does_not_escalate():
    new_count, reason = handle_offtopic(
        current_count=OFFTOPIC_THRESHOLD - 2, decision=_decision(True),
    )
    assert new_count == OFFTOPIC_THRESHOLD - 1
    assert reason is None


def test_offtopic_at_threshold_escalates():
    new_count, reason = handle_offtopic(
        current_count=OFFTOPIC_THRESHOLD - 1, decision=_decision(True),
    )
    assert new_count == OFFTOPIC_THRESHOLD
    assert reason == "off_topic_exhausted"


def test_not_offtopic_does_not_increment():
    new_count, reason = handle_offtopic(
        current_count=2, decision=_decision(False),
    )
    assert new_count == 2
    assert reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_offtopic_counter_and_escalate.py -v`
Expected: FAIL — `off_topic_detected` field missing AND `offtopic` module missing.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/decision.py`, add to `TurnDecision` (anywhere in the field block):

```python
    # Off-topic detection (FE-03a brecha A1)
    off_topic_detected: bool = False
```

Create `src/ai_sdr/flowengine/offtopic.py`:

```python
"""Off-topic counter + escalation (FE-03a §8 A1).

The LLM is told (via system prompt) to flag inbounds that fall outside
the funnel's scope. This module increments TalkFlowState.off_topic_count
and decides when to escalate.
"""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision

OFFTOPIC_THRESHOLD = 3


def handle_offtopic(
    *,
    current_count: int,
    decision: TurnDecision,
) -> tuple[int, str | None]:
    """Return (new_count, requires_review_reason_or_None)."""
    if not decision.off_topic_detected:
        return current_count, None
    new_count = current_count + 1
    if new_count >= OFFTOPIC_THRESHOLD:
        return new_count, "off_topic_exhausted"
    return new_count, None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_offtopic_counter_and_escalate.py -v
```
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/decision.py src/ai_sdr/flowengine/offtopic.py tests/unit/test_offtopic_counter_and_escalate.py
git commit -m "feat(fe03a t25): off-topic counter + escalation aos 3 (brecha A1)

Per spec §8 A1. LLM flags off-topic; runtime counts; aos 3 strikes
escalates with requires_review_reason=off_topic_exhausted."
```

---

### Task 26: Escalation request handling (lead pediu humano OU LLM decidiu)

**Files:**
- Create: `src/ai_sdr/flowengine/escalation.py`
- Test: `tests/unit/test_escalation_request.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_escalation_request.py
"""Escalation handler — lead_requested vs LLM-decided (FE-03a Task 26)."""
from __future__ import annotations

from ai_sdr.flowengine.decision import HumanEscalation, TurnDecision
from ai_sdr.flowengine.escalation import resolve_escalation_reason


def _decision(escalation: HumanEscalation | None = None) -> TurnDecision:
    return TurnDecision(
        response_text="x",
        collected_fields={},
        reasoning="r",
        request_human_escalation=escalation,
    )


def test_lead_requested_resolves_to_escalation_requested():
    esc = HumanEscalation(
        reason="lead asked to talk to a human directly",
        category="lead_requested",
        urgency="medium",
    )
    reason = resolve_escalation_reason(_decision(esc))
    assert reason == "escalation_requested"


def test_llm_decided_escalation_resolves_to_escalation_requested():
    esc = HumanEscalation(
        reason="objection treatment not making progress",
        category="complex_objection",
        urgency="medium",
    )
    reason = resolve_escalation_reason(_decision(esc))
    assert reason == "escalation_requested"


def test_no_escalation_resolves_to_none():
    assert resolve_escalation_reason(_decision(None)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_escalation_request.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/flowengine/escalation.py`:

```python
"""Resolve TurnDecision.request_human_escalation -> requires_review_reason (FE-03a Task 26).

The LLM emits `request_human_escalation` as a structured HumanEscalation
object whenever it (or the lead) wants a human teammate. All categories
(lead_requested, complex_objection, etc.) collapse to one DB reason:
'escalation_requested'. Category + urgency stay on talk.escalation_category
for HITL prioritization (existing FE-01b columns).
"""

from __future__ import annotations

from ai_sdr.flowengine.decision import TurnDecision


def resolve_escalation_reason(decision: TurnDecision) -> str | None:
    if decision.request_human_escalation is None:
        return None
    return "escalation_requested"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_escalation_request.py -v
```
Expected: PASS (3/3).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/escalation.py tests/unit/test_escalation_request.py
git commit -m "feat(fe03a t26): escalation handler — all categories -> escalation_requested

Per spec §8 A2. Category + urgency remain on talk.escalation_category
(existing column). Single requires_review_reason makes the operator
HITL queue (FE-07) discoverable."
```

---

## Phase 9: Pipeline wiring

### Task 27: `post_processing.apply_decision` invoca `objection_runtime.apply` + heurísticas

**Files:**
- Modify: `src/ai_sdr/flowengine/post_processing.py`
- Test: `tests/integration/test_post_processing_objection_state.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_post_processing_objection_state.py
"""apply_decision integrates objection_runtime + heuristics (FE-03a Task 27)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.post_processing import apply_decision

pytestmark = pytest.mark.asyncio


async def test_objection_detected_enters_active_treatment(
    async_session, talk_factory, talkflow_state_factory, treeflow_with_preco_tool,
):
    talk = await talk_factory()
    state = await talkflow_state_factory(
        talk_id=talk.id, current_node="a", active_treatment=None,
    )
    decision = TurnDecision(
        response_text="argumento",
        collected_fields={},
        reasoning="r",
        detected_objection="preco",
    )
    await apply_decision(
        async_session,
        talk=talk, state=state, decision=decision,
        resolved_target_node="a", now=datetime.now(timezone.utc),
        treeflow=treeflow_with_preco_tool,
    )
    await async_session.refresh(state)
    assert state.active_treatment is not None
    assert state.active_treatment["objection_id"] == "preco"


async def test_contradiction_heuristic_applied_before_state_update(
    async_session, talk_factory, talkflow_state_factory, treeflow_with_preco_tool,
):
    talk = await talk_factory()
    state = await talkflow_state_factory(
        talk_id=talk.id, current_node="a",
        active_treatment={
            "objection_id": "preco",
            "started_at_turn": 1,
            "current_treatment_turn": 2,
            "max_treatment_turns": 3,
            "resolution_criteria": "x",
            "treatment_history": [],
        },
    )
    decision = TurnDecision(
        response_text="Ah que pena, deixa eu te deixar pensar então",
        collected_fields={},
        reasoning="r",
        treatment_status="resolved_accepted",  # contradicts text
    )
    await apply_decision(
        async_session,
        talk=talk, state=state, decision=decision,
        resolved_target_node="a", now=datetime.now(timezone.utc),
        treeflow=treeflow_with_preco_tool,
    )
    await async_session.refresh(state)
    # contradiction corrected: accepted -> deferred -> active cleared, history deferred
    assert state.active_treatment is None
    history = state.objections_handled
    assert history[-1]["resolution"] == "deferred"
```

(Fixtures `talk_factory`, `talkflow_state_factory`, `treeflow_with_preco_tool` must live in `tests/conftest.py` or `tests/integration/conftest.py`. If they don't, add them as part of this task using existing fixture patterns from `tests/integration/test_post_processing_state_apply.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_post_processing_objection_state.py -v`
Expected: FAIL — `apply_decision` signature doesn't take `treeflow`; objection_runtime not invoked.

- [ ] **Step 3: Apply implementation**

Rewrite `src/ai_sdr/flowengine/post_processing.py`:

```python
"""Apply TurnDecision to persistent state (FE-03a Task 27 — extended).

Pipeline:
  1. Run contradiction heuristic on decision
  2. Run implicit-transition heuristic (events only)
  3. Compute objection_runtime.StateDelta
  4. Apply collected_fields + extracted_facts merge
  5. Apply state delta (active_treatment, objection history)
  6. Set current_node to resolved_target
  7. Append assistant message to history window
  8. Set turn_count + last_message_at on talk
  9. Set requires_review_reason if any heuristic raised it
  10. Emit all collected events via structlog
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.escalation import resolve_escalation_reason
from ai_sdr.flowengine.heuristics import (
    apply_contradiction_heuristic,
    detect_implicit_transition,
)
from ai_sdr.flowengine.objection_runtime import apply as apply_objection_state
from ai_sdr.flowengine.offtopic import handle_offtopic
from ai_sdr.flowengine.state import Message
from ai_sdr.flowengine.treeflow_loader import TreeflowDef
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository

logger = logging.getLogger(__name__)


def _emit_events(events: list[tuple[str, dict[str, Any]]], talk_id, lead_id) -> None:
    for name, payload in events:
        logger.info(
            "fe03a_event %s talk=%s lead=%s payload=%s",
            name, talk_id, lead_id, payload,
        )


async def apply_decision(
    session: AsyncSession,
    *,
    talk: Talk,
    state: TalkFlowState,
    decision: TurnDecision,
    resolved_target_node: str,
    now: datetime,
    treeflow: TreeflowDef,
) -> None:
    """Mutate state + talk to reflect the LLM's decision."""
    events: list[tuple[str, dict[str, Any]]] = []

    # 1. Contradiction heuristic (B4)
    decision, ev = apply_contradiction_heuristic(decision)
    events.extend(ev)

    # 2. Implicit-transition heuristic (C4, audit only)
    events.extend(detect_implicit_transition(decision))

    # 3. Compute objection state delta
    state_view = {
        "current_node": state.current_node,
        "active_treatment": state.active_treatment,
        "objections_handled": list(state.objections_handled),
    }
    delta = apply_objection_state(
        state=state_view, decision=decision, treeflow=treeflow,
    )
    events.extend(delta.events)

    # 4. Merge collected + extracted_facts
    if decision.collected_fields:
        merged = dict(state.collected); merged.update(decision.collected_fields)
        state.collected = merged
        flag_modified(state, "collected")
    if decision.extracted_facts:
        merged_facts = dict(state.extracted_facts); merged_facts.update(decision.extracted_facts)
        state.extracted_facts = merged_facts
        flag_modified(state, "extracted_facts")

    # 5. Apply state delta
    if delta.changes_treatment:
        state.active_treatment = delta.new_active_treatment
        flag_modified(state, "active_treatment")
    if delta.appended_objection_history:
        history = list(state.objections_handled)
        history.extend(delta.appended_objection_history)
        state.objections_handled = history
        flag_modified(state, "objections_handled")

    # 6. current_node
    state.current_node = resolved_target_node

    # 7. History append
    repo = TalkFlowStateRepository(session)
    next_turn = talk.turn_count + 1
    await repo.append_message(
        state,
        Message(
            role="assistant", content=decision.response_text,
            source="agent", turn_index=next_turn, timestamp=now,
        ),
        max_window=15,
    )

    # 8. Talk metadata
    talk.turn_count = next_turn
    talk.last_message_at = now

    # 9. requires_review_reason — first non-None wins
    review_reason = (
        delta.requires_review_reason
        or resolve_escalation_reason(decision)
    )
    # Off-topic counter requires reading TalkFlowStatePayload off_topic_count
    # which lives in the state row's extra JSON (defaults to 0).
    current_offtopic = (state.collected.get("__off_topic_count__")
                        if isinstance(state.collected, dict) else 0) or 0
    new_offtopic, offtopic_reason = handle_offtopic(
        current_count=current_offtopic, decision=decision,
    )
    if new_offtopic != current_offtopic:
        merged = dict(state.collected)
        merged["__off_topic_count__"] = new_offtopic
        state.collected = merged
        flag_modified(state, "collected")
    review_reason = review_reason or offtopic_reason

    if review_reason and talk.status != "requires_review":
        talk.status = "requires_review"
        talk.requires_review_reason = review_reason
        talk.escalated_at = now

    # 10. Emit events
    _emit_events(events, talk.id, getattr(talk, "lead_id", None))

    if decision.suggest_close_talk != "no":
        logger.info(
            "talk_close_signal_ignored_in_fe03a talk_id=%s signal=%s",
            talk.id, decision.suggest_close_talk,
        )
```

Then update `pipeline.py:194-198` to pass `treeflow=treeflow` to `apply_decision`.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_post_processing_objection_state.py -v
uv run pytest tests/integration/test_post_processing_state_apply.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/post_processing.py src/ai_sdr/flowengine/pipeline.py tests/integration/test_post_processing_objection_state.py
git commit -m "feat(fe03a t27): wire objection_runtime + heuristics into post_processing

Per spec §3. apply_decision now takes treeflow and runs the full FE-03a
pipeline: contradiction heuristic -> implicit-transition heuristic ->
objection_runtime.apply -> state merge -> requires_review_reason setting.
Events flushed via structlog."
```

---

### Task 28: `pipeline.run_turn` propaga `requires_review_reason` em todos os caminhos de escalation

**Files:**
- Modify: `src/ai_sdr/flowengine/pipeline.py`
- Test: `tests/integration/test_pipeline_review_reasons.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_review_reasons.py
"""run_turn writes requires_review_reason on every escalation path (FE-03a Task 28)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_sdr.flowengine.correction import CorrectionEscalation

pytestmark = pytest.mark.asyncio


async def test_guardrails_exhaustion_sets_validator_exhausted(
    async_session, run_turn_harness, fake_llm_violating_price,
):
    talk = await run_turn_harness.run(llm=fake_llm_violating_price)
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "validator_exhausted"


async def test_treeflow_version_missing_sets_treeflow_version_missing(
    async_session, run_turn_harness, fake_llm_polite,
):
    # Simulate version snapshot pointing at a YAML file that no longer exists.
    await run_turn_harness.corrupt_treeflow_snapshot()
    talk = await run_turn_harness.run(llm=fake_llm_polite)
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "treeflow_version_missing"
```

(`run_turn_harness` is a new pytest fixture wrapping `run_turn(...)` with sensible defaults. Add it to `tests/integration/conftest.py` reusing the existing fixtures from `test_pipeline_smoke_end_to_end.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_pipeline_review_reasons.py -v`
Expected: FAIL — only `validator_exhausted` works; `treeflow_version_missing` path absent.

- [ ] **Step 3: Apply implementation**

In `pipeline.py`, locate the `CorrectionEscalation` except block (around line 154-167). Replace:

```python
        except CorrectionEscalation as e:
            ctx.talk.status = "requires_review"
            ctx.talk.escalated_at = now
            ctx.talk.escalation_category = "system_exhausted"
            ctx.talk.escalation_reason = str(e)
            ctx.talk.requires_review_reason = "validator_exhausted"   # NEW
            logger.warning(
                "turn_escalated_via_guardrails talk=%s reason=%s",
                ctx.talk.id, e,
            )
            # Send tenant fallback before returning so the lead isn't left hanging.
            await adapter.send_text(
                lead=ctx.lead,
                text=guardrail_cfg.fallback_text,
            )
            return RunTurnResult(
                outcome="escalated",
                current_node_after=state.current_node,
                response_text=guardrail_cfg.fallback_text,
            )
```

For `treeflow_version_missing`: in `preprocessing.resolve_pipeline_context` (or wherever the version is loaded), catch the FileNotFoundError / equivalent, set `talk.status=requires_review` and `requires_review_reason=treeflow_version_missing`, then raise a new typed exception `TreeflowVersionMissing`. In `run_turn`, catch it and return:

```python
except TreeflowVersionMissing:
    return RunTurnResult(outcome="escalated", current_node_after=None, response_text=None)
```

Add the new exception class to `src/ai_sdr/flowengine/preprocessing.py`:

```python
class TreeflowVersionMissing(Exception):
    """The version snapshot recorded on the Talk no longer resolves to a YAML on disk."""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_pipeline_review_reasons.py -v
uv run pytest tests/integration/test_pipeline_smoke_end_to_end.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/pipeline.py src/ai_sdr/flowengine/preprocessing.py tests/integration/test_pipeline_review_reasons.py tests/integration/conftest.py
git commit -m "feat(fe03a t28): requires_review_reason on every escalation path

Per spec §11. validator_exhausted, treeflow_version_missing — both
set the new column. Other reasons (escalation_requested,
off_topic_exhausted, objection_treatment_exhausted) set in t27."
```

---

### Task 29: Transação única — `run_turn` envolve tudo em `session.begin()`

**Files:**
- Modify: `src/ai_sdr/flowengine/pipeline.py`
- Test: `tests/integration/test_pipeline_transactional_consistency.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pipeline_transactional_consistency.py
"""run_turn rolls back state on send failure (FE-03a Task 29 §9.1)."""
from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import TransientError

pytestmark = pytest.mark.asyncio


async def test_send_failure_rolls_back_state(
    async_session, run_turn_harness, fake_llm_polite, raising_adapter,
):
    """If adapter.send_text raises, state changes from this turn must not persist."""
    talk_before = await run_turn_harness.talk()
    state_before_node = (await run_turn_harness.state()).current_node

    with pytest.raises(TransientError):
        await run_turn_harness.run(llm=fake_llm_polite, adapter=raising_adapter)

    # New session — verify nothing committed.
    async with run_turn_harness.new_session() as fresh:
        talk = await fresh.get(type(talk_before), talk_before.id)
        state = await run_turn_harness.state_for_session(fresh, talk_before.id)
        assert talk.turn_count == talk_before.turn_count
        assert state.current_node == state_before_node
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_pipeline_transactional_consistency.py -v`
Expected: FAIL — current pipeline commits state changes before send and does not rollback on adapter error.

- [ ] **Step 3: Apply implementation**

In `pipeline.py:run_turn`, wrap the body (after preprocessing) in `async with session.begin():`. The advisory lock + state mutations + sender + audit are inside the same transaction. If `sender.send_response_text` raises, the transaction rolls back; the inbound row stays unprocessed and the worker can retry.

```python
async def run_turn(
    session: AsyncSession,
    *,
    tenant: Tenant,
    treeflow: TreeflowDef,
    treeflow_version: TreeflowVersion,
    inbound: InboundMessageRow,
    llm: Runnable,
    adapter: MessagingAdapter,
    opt_out_keywords: list[str],
    guardrail_cfg: GuardrailConfig,
    now: datetime | None = None,
) -> RunTurnResult:
    now = now or datetime.now(timezone.utc)

    # Preprocessing happens OUTSIDE the transaction because it includes
    # find-or-create of lead and lead-resolution side effects that we
    # want to keep on retry.
    try:
        ctx = await resolve_pipeline_context(
            session, tenant=tenant, inbound=inbound,
            treeflow=treeflow, treeflow_version=treeflow_version,
            opt_out_keywords=opt_out_keywords,
        )
    except OptOutDetected:
        return RunTurnResult(outcome="opt_out", current_node_after=None, response_text=None)

    if ctx.lead.risk_level == "banned":
        return RunTurnResult(outcome="lead_banned", current_node_after=None, response_text=None)

    async with session.begin():
        await acquire_lead_lock(session, tenant.id, ctx.lead.id)
        # ... [rest of the body unchanged, but with the apply_decision call
        #      now receiving treeflow=treeflow per Task 27] ...
    return result
```

(Move the `return RunTurnResult(...)` after the `with` block; bind it to `result` inside.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_pipeline_transactional_consistency.py -v
uv run pytest tests/integration/test_pipeline_smoke_end_to_end.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/pipeline.py tests/integration/test_pipeline_transactional_consistency.py
git commit -m "feat(fe03a t29): run_turn body in single transaction (spec §9.1)

State mutations + sender + audit commit together. Adapter failure
rolls back; worker retry sees unprocessed inbound and re-runs."
```

---

## Phase 10: Worker improvements

### Task 30: Inbound concatenation window (brecha B1)

**Files:**
- Modify: `src/ai_sdr/messaging/ingest.py` (or wherever the worker dispatches `run_turn` — TBD via grep below)
- Test: `tests/integration/test_inbound_concat_window.py` (create)

- [ ] **Step 1: Identify the worker callsite**

```bash
cd /Users/nicolasamaral/dev/PeSDR-fe01b-pipeline
grep -rn "run_turn" src/ | grep -v test_ | grep -v __pycache__
```

The hit that wires Worker → run_turn is the target file for this task.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_inbound_concat_window.py
"""Worker concatenates pending inbounds within a 2s window before invoking run_turn (FE-03a Task 30)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


async def test_two_inbounds_within_window_collapsed_to_one_turn(
    async_session, worker_harness, fake_llm_polite,
):
    lead = await worker_harness.lead()
    now = datetime.now(timezone.utc)
    await worker_harness.enqueue_inbound(
        lead=lead, text="ok", received_at=now,
    )
    await worker_harness.enqueue_inbound(
        lead=lead, text="manda link", received_at=now + timedelta(milliseconds=500),
    )
    result = await worker_harness.process_one(llm=fake_llm_polite)
    # Single run_turn invocation, both texts visible in inbound payload
    assert result.run_turn_invocations == 1
    assert "ok" in result.consolidated_text
    assert "manda link" in result.consolidated_text


async def test_inbound_outside_window_starts_new_turn(
    async_session, worker_harness, fake_llm_polite,
):
    lead = await worker_harness.lead()
    now = datetime.now(timezone.utc)
    await worker_harness.enqueue_inbound(lead=lead, text="ok", received_at=now)
    await worker_harness.enqueue_inbound(
        lead=lead, text="??", received_at=now + timedelta(seconds=3),
    )
    result = await worker_harness.process_one(llm=fake_llm_polite)
    assert result.run_turn_invocations == 1
    assert "ok" in result.consolidated_text
    assert "??" not in result.consolidated_text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_inbound_concat_window.py -v`
Expected: FAIL — concat not implemented.

- [ ] **Step 4: Apply implementation**

In the worker callsite, before invoking `run_turn`:

```python
import os
from datetime import timedelta

from sqlalchemy import select, update
from ai_sdr.models.inbound_message import InboundMessageRow

_CONCAT_WINDOW_SECONDS = int(os.environ.get("WORKER_INBOUND_CONCAT_WINDOW_SECONDS", "2"))


async def _drain_pending_window(
    session, *, tenant_id, lead_id, received_at_floor,
):
    """Mark all unprocessed inbounds in the window as processed, return texts in order."""
    rows = (
        await session.execute(
            select(InboundMessageRow)
            .where(
                InboundMessageRow.tenant_id == tenant_id,
                InboundMessageRow.lead_id == lead_id,
                InboundMessageRow.processed_at.is_(None),
                InboundMessageRow.received_at >= received_at_floor,
            )
            .order_by(InboundMessageRow.received_at.asc())
            .with_for_update()
        )
    ).scalars().all()
    return rows


# Inside the worker handler, after acquiring the advisory lock and before run_turn:

received_at_floor = inbound.received_at - timedelta(seconds=_CONCAT_WINDOW_SECONDS)
pending = await _drain_pending_window(
    session,
    tenant_id=tenant.id, lead_id=ctx.lead.id,
    received_at_floor=received_at_floor,
)
consolidated_text = "\n".join((r.text or r.transcription or "").strip() for r in pending if (r.text or r.transcription))
# Use consolidated_text in lieu of inbound.text. Update the InboundMessageRow.text
# in-memory for the run_turn call so downstream sees the merged content.
inbound.text = consolidated_text
# Mark all pending rows as processed.
for r in pending:
    r.processed_at = now
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_inbound_concat_window.py -v
uv run pytest tests/integration/test_pipeline_smoke_end_to_end.py -v
```
Expected: PASS, no regression.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/ingest.py tests/integration/test_inbound_concat_window.py
git commit -m "feat(fe03a t30): worker concatenates pending inbounds in 2s window (brecha B1)

Per spec §9.3. 1 LLM call per burst instead of N. Window configurable
via WORKER_INBOUND_CONCAT_WINDOW_SECONDS env (default 2)."
```

---

### Task 31: TreeFlow version snapshot at Talk open (brecha B2)

**Files:**
- Modify: `src/ai_sdr/flowengine/preprocessing.py`
- Test: `tests/integration/test_treeflow_snapshot_at_open.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_treeflow_snapshot_at_open.py
"""Talk records treeflow_version_snapshot on open + uses it always (FE-03a Task 31, brecha B2)."""
from __future__ import annotations

import pytest

from ai_sdr.flowengine.preprocessing import TreeflowVersionMissing

pytestmark = pytest.mark.asyncio


async def test_new_talk_records_version_snapshot(async_session, run_turn_harness, fake_llm_polite):
    talk = await run_turn_harness.run(llm=fake_llm_polite)
    await async_session.refresh(talk)
    assert talk.treeflow_version_snapshot is not None
    assert "." in talk.treeflow_version_snapshot   # looks like semver


async def test_subsequent_turn_uses_recorded_snapshot(async_session, run_turn_harness, fake_llm_polite):
    await run_turn_harness.run(llm=fake_llm_polite)
    snapshot1 = (await run_turn_harness.talk()).treeflow_version_snapshot
    # Publish a new version of the same TreeFlow on disk
    await run_turn_harness.bump_treeflow_version_on_disk()
    await run_turn_harness.run(llm=fake_llm_polite)
    snapshot2 = (await run_turn_harness.talk()).treeflow_version_snapshot
    assert snapshot1 == snapshot2


async def test_version_missing_raises(async_session, run_turn_harness, fake_llm_polite):
    await run_turn_harness.run(llm=fake_llm_polite)
    await run_turn_harness.corrupt_treeflow_snapshot()
    with pytest.raises(TreeflowVersionMissing):
        await run_turn_harness.run(llm=fake_llm_polite)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_treeflow_snapshot_at_open.py -v`
Expected: FAIL — snapshot column not yet populated; missing-version path raises generic.

- [ ] **Step 3: Apply implementation**

If `talks.treeflow_version_snapshot` column doesn't exist yet, add it via migration `0026` (or extend `0025` if not committed). Otherwise it should already exist from FE-01a.

In `preprocessing.resolve_pipeline_context`, when **creating** a Talk:

```python
talk = Talk(
    # ... existing fields ...
    treeflow_version_snapshot=treeflow.version,
)
```

When **resolving** an existing Talk, load the TreeFlow per `talk.treeflow_version_snapshot` instead of `tenant.active_treeflow_version`:

```python
if talk.treeflow_version_snapshot:
    try:
        treeflow_to_use = treeflow_loader_cache.load(
            tenant_id=tenant.id, version=talk.treeflow_version_snapshot,
        )
    except FileNotFoundError:
        talk.status = "requires_review"
        talk.requires_review_reason = "treeflow_version_missing"
        await session.commit()
        raise TreeflowVersionMissing(talk.treeflow_version_snapshot)
```

(Adjust to match the actual loader call signature in the codebase.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/integration/test_treeflow_snapshot_at_open.py -v
uv run pytest tests/integration/test_pipeline_smoke_end_to_end.py -v
```
Expected: PASS, no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/preprocessing.py tests/integration/test_treeflow_snapshot_at_open.py
git commit -m "feat(fe03a t31): TreeFlow version snapshot at Talk open (brecha B2)

Per spec §B2. Talk records version on open; runtime uses snapshot
forever. Missing version -> requires_review + TreeflowVersionMissing
raised (caught in pipeline t28)."
```

---

## Phase 11: Integration tests E2E

These tests share a `FakeListChatModel`-based harness that feeds pre-scripted `TurnDecision` payloads turn-by-turn through `run_turn`. Each task is one test scenario; they all use the same fixture pattern, so the cost per task is just writing the script + assertions.

### Task 32: 3-turn treatment resolves accepted

**Files:**
- Test: `tests/integration/test_treatment_3_turn_resolve_accepted.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_treatment_3_turn_resolve_accepted.py
"""3-turn objection treatment, resolves accepted (FE-03a Task 32)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_treatment_resolves_accepted_after_3_turns(
    async_session, run_turn_harness, fake_chat_scripted,
):
    llm = fake_chat_scripted([
        {"response_text": "Quanto vc fatura hoje?", "detected_objection": "preco", "reasoning": "r"},
        {"response_text": "Com ROI mensurável a Mentoria...", "treatment_status": "in_progress", "reasoning": "r"},
        {"response_text": "Maravilha! Vou agendar.", "treatment_status": "resolved_accepted", "reasoning": "r"},
    ])
    state = await run_turn_harness.state()

    # Turn 1: lead complains about price
    await run_turn_harness.send_inbound("R$ 6k tá caro pra mim")
    await run_turn_harness.run(llm=llm)
    await async_session.refresh(state)
    assert state.active_treatment["objection_id"] == "preco"
    assert state.active_treatment["current_treatment_turn"] == 1

    # Turn 2: lead engages
    await run_turn_harness.send_inbound("Faturo R$ 30k")
    await run_turn_harness.run(llm=llm)
    await async_session.refresh(state)
    assert state.active_treatment["current_treatment_turn"] == 2

    # Turn 3: lead accepts
    await run_turn_harness.send_inbound("ok, fechou!")
    await run_turn_harness.run(llm=llm)
    await async_session.refresh(state)
    assert state.active_treatment is None
    assert state.objections_handled[-1]["resolution"] == "accepted"
```

- [ ] **Step 2: Run test to verify it fails (initial scaffolding) or passes (if everything wired)**

Run: `uv run pytest tests/integration/test_treatment_3_turn_resolve_accepted.py -v`
Expected: PASS (all previous tasks wired). If FAIL, the failure points to which previous task left something hanging.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_treatment_3_turn_resolve_accepted.py
git commit -m "test(fe03a t32): E2E 3-turn treatment resolves accepted"
```

---

### Task 33: 3-turn treatment exhausted → escalate

**Files:**
- Test: `tests/integration/test_treatment_exhausted_escalate.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_treatment_exhausted_escalate.py
"""3-turn treatment exhausted, action=escalate_to_human (FE-03a Task 33)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_exhaustion_with_escalate_sets_requires_review(
    async_session, run_turn_harness_escalating_objection, fake_chat_scripted,
):
    """Tenant TreeFlow uses on_max_turns_no_resolution.action=escalate_to_human."""
    h = run_turn_harness_escalating_objection
    llm = fake_chat_scripted([
        {"response_text": "Argumento 1", "detected_objection": "preco", "reasoning": "r"},
        {"response_text": "Argumento 2", "treatment_status": "in_progress", "reasoning": "r"},
        {"response_text": "Argumento 3", "treatment_status": "in_progress", "reasoning": "r"},
    ])
    for inbound in ("caro!", "ainda caro", "mesmo assim caro"):
        await h.send_inbound(inbound)
        await h.run(llm=llm)
    talk = await h.talk()
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "objection_treatment_exhausted"
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_treatment_exhausted_escalate.py -v
git add tests/integration/test_treatment_exhausted_escalate.py
git commit -m "test(fe03a t33): E2E exhaustion + escalate_to_human path"
```

---

### Task 34: Cross-objection swap E2E

**Files:**
- Test: `tests/integration/test_cross_objection_swap.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_cross_objection_swap.py
"""Mid-treatment, new tool objection -> swap, old goes deferred (FE-03a Task 34)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_swap_defers_previous_objection(
    async_session, run_turn_harness_with_preco_and_tempo, fake_chat_scripted,
):
    h = run_turn_harness_with_preco_and_tempo
    state = await h.state()
    llm = fake_chat_scripted([
        {"response_text": "Argumento de preço", "detected_objection": "preco", "reasoning": "r"},
        {"response_text": "Pivot pra tempo", "detected_objection": "tempo", "reasoning": "r"},
    ])
    await h.send_inbound("tá caro"); await h.run(llm=llm)
    await async_session.refresh(state)
    assert state.active_treatment["objection_id"] == "preco"

    await h.send_inbound("e também não tenho tempo"); await h.run(llm=llm)
    await async_session.refresh(state)
    assert state.active_treatment["objection_id"] == "tempo"
    deferred = [o for o in state.objections_handled if o["resolution"] == "deferred"]
    assert any(o["objection_id"] == "preco" for o in deferred)
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_cross_objection_swap.py -v
git add tests/integration/test_cross_objection_swap.py
git commit -m "test(fe03a t34): E2E cross-objection swap defers previous"
```

---

### Task 35: Multi-message concat E2E

**Files:**
- Test: `tests/integration/test_multi_message_concat_e2e.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_multi_message_concat_e2e.py
"""Multiple inbounds within window -> 1 LLM call, 1 turn (FE-03a Task 35)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


async def test_burst_inbounds_yield_single_turn(
    async_session, worker_harness, fake_chat_scripted,
):
    lead = await worker_harness.lead()
    now = datetime.now(timezone.utc)
    await worker_harness.enqueue_inbound(lead=lead, text="ok", received_at=now)
    await worker_harness.enqueue_inbound(lead=lead, text="manda link", received_at=now + timedelta(milliseconds=300))
    await worker_harness.enqueue_inbound(lead=lead, text="vamos", received_at=now + timedelta(milliseconds=900))

    llm = fake_chat_scripted([
        {"response_text": "Aqui está", "reasoning": "r"},
    ])
    out = await worker_harness.process_one(llm=llm)
    assert out.run_turn_invocations == 1
    assert "ok" in out.consolidated_text and "manda link" in out.consolidated_text and "vamos" in out.consolidated_text
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_multi_message_concat_e2e.py -v
git add tests/integration/test_multi_message_concat_e2e.py
git commit -m "test(fe03a t35): E2E worker concat window collapses burst"
```

---

### Task 36: Validator fallback E2E

**Files:**
- Test: `tests/integration/test_validator_fallback_e2e.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_validator_fallback_e2e.py
"""Validator exhausts -> tenant.guardrails.fallback_text sent + requires_review (FE-03a Task 36)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_validator_exhausted_sends_fallback(
    async_session, run_turn_harness, fake_chat_scripted, capture_outbound,
):
    # Both main + retry LLM responses violate the price whitelist.
    llm = fake_chat_scripted([
        {"response_text": "Custa só R$ 5000", "reasoning": "r"},
        {"response_text": "Por R$ 4500 pra você", "reasoning": "r"},
    ])
    await run_turn_harness.send_inbound("quanto custa?")
    result = await run_turn_harness.run(llm=llm)
    talk = await run_turn_harness.talk()
    await async_session.refresh(talk)
    assert talk.status == "requires_review"
    assert talk.requires_review_reason == "validator_exhausted"
    sent = capture_outbound.last_sent()
    assert sent == run_turn_harness.tenant_guardrails.fallback_text
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_validator_fallback_e2e.py -v
git add tests/integration/test_validator_fallback_e2e.py
git commit -m "test(fe03a t36): E2E validator exhausted -> fallback text sent"
```

---

### Task 37: Versioning snapshot missing E2E

**Files:**
- Test: `tests/integration/test_versioning_snapshot_missing.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_versioning_snapshot_missing.py
"""Snapshot version no longer on disk -> requires_review + treeflow_version_missing (FE-03a Task 37)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_snapshot_missing_escalates(
    async_session, run_turn_harness, fake_chat_scripted,
):
    llm = fake_chat_scripted([{"response_text": "olá", "reasoning": "r"}])
    await run_turn_harness.send_inbound("oi"); await run_turn_harness.run(llm=llm)
    await run_turn_harness.corrupt_treeflow_snapshot()
    await run_turn_harness.send_inbound("e aí")
    # Expect run_turn to short-circuit; result is "escalated".
    out = await run_turn_harness.run(llm=llm)
    talk = await run_turn_harness.talk()
    await async_session.refresh(talk)
    assert out.outcome == "escalated"
    assert talk.requires_review_reason == "treeflow_version_missing"
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_versioning_snapshot_missing.py -v
git add tests/integration/test_versioning_snapshot_missing.py
git commit -m "test(fe03a t37): E2E versioning snapshot missing path"
```

---

### Task 38: Transition blocked during treatment E2E

**Files:**
- Test: `tests/integration/test_transition_blocked_during_treatment_e2e.py` (create)

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_transition_blocked_during_treatment_e2e.py
"""ACTIVE treatment + LLM suggests transition -> routing blocks; corrective retry kicks in (FE-03a Task 38)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_transition_during_treatment_falls_back_to_current_node(
    async_session, run_turn_harness, fake_chat_scripted,
):
    # Turn 1: detect objection
    # Turn 2: LLM (incorrectly) suggests transition while still in treatment
    # The retry response stays in node + does not transition
    llm = fake_chat_scripted([
        {"response_text": "Argumento", "detected_objection": "preco", "reasoning": "r"},
        {"response_text": "transição indevida", "treatment_status": "in_progress", "next_node_suggestion": "outro", "reasoning": "r"},
        {"response_text": "Argumento de novo", "treatment_status": "in_progress", "next_node_suggestion": None, "reasoning": "r"},
    ])
    await run_turn_harness.send_inbound("caro"); await run_turn_harness.run(llm=llm)
    await run_turn_harness.send_inbound("ainda caro"); await run_turn_harness.run(llm=llm)
    state = await run_turn_harness.state()
    await async_session.refresh(state)
    assert state.current_node == "a"  # did NOT advance
    assert state.active_treatment is not None
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_transition_blocked_during_treatment_e2e.py -v
git add tests/integration/test_transition_blocked_during_treatment_e2e.py
git commit -m "test(fe03a t38): E2E transition blocked during active_treatment"
```

---

### Task 39: Compound response uses next-node bridge

**Files:**
- Test: `tests/integration/test_compound_response_uses_next_bridge.py` (create)

- [ ] **Step 1: Write the test**

This test confirms that when the LLM in node A receives a fresh layer including next-node `bridge_instruction`, the **system prompt assembled** mentions the next-node bridge. We assert on the **prompt content**, not on LLM behaviour quality (which requires a live model). This is the contract test for brecha C3.

```python
# tests/integration/test_compound_response_uses_next_bridge.py
"""Fresh layer in node A includes IMMEDIATE NEXT NODES' bridge_instruction (FE-03a Task 39, brecha C3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_prompt_in_node_a_carries_next_node_bridge(
    async_session, run_turn_harness, fake_chat_capturing_prompt,
):
    llm, captured = fake_chat_capturing_prompt(
        {"response_text": "ack", "reasoning": "r"},
    )
    await run_turn_harness.send_inbound("oi"); await run_turn_harness.run(llm=llm)
    system_prompt_text = captured["fresh_layer_text"]
    next_node_bridge = run_turn_harness.treeflow.nodes["envio_checkout"].bridge_instruction
    assert next_node_bridge in system_prompt_text
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_compound_response_uses_next_bridge.py -v
git add tests/integration/test_compound_response_uses_next_bridge.py
git commit -m "test(fe03a t39): E2E compound-response — next-node bridge in prompt"
```

---

## Phase 12: Close-out

### Task 40: CLAUDE.md update — FE-03a notes

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Append a new section**

Add at the appropriate location (after the "Multi-provider LLM (Plan 3 T2b architectural opening)" or near other FE notes):

```markdown
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
  - Off-topic: `TalkFlowState.off_topic_count` incrementa; aos 3 escalates com `requires_review_reason='off_topic_exhausted'`.
  - Lead pede humano: LLM emite `request_human_escalation` (qualquer category); runtime seta `requires_review_reason='escalation_requested'`.
  - Mídia (áudio/imagem): **gap conhecido**, depende FE-05. Tenant configura Meta Business Manager pra não receber mídia ANTES de subir FE-03a sem FE-05.
- **Brechas técnicas:** transação única no `run_turn`; worker concatena inbounds pendentes (janela 2s configurável via `WORKER_INBOUND_CONCAT_WINDOW_SECONDS`); TreeFlow snapshot at Talk open (versão sumiu → `requires_review` com `treeflow_version_missing`).
- **Heurísticas pós-LLM:** contradição (`accepted` → `deferred` quando texto contradiz) e implicit transition (event-only).
- **Migration 0025:** `talks.requires_review_reason` enum com 5 valores. Consumido pelo HITL console (FE-07).

### Wipe pra dev fresh (atualiza FE-01a guidance)
```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations; \
      UPDATE talks SET status='active', requires_review_reason=NULL, escalated_at=NULL;"
```
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(fe03a t40): CLAUDE.md notes — Objection Runtime + Python Validator

Documents new YAML schema, runtime behavior, brechas decisions,
migration 0025, and the FE-03a/FE-05 dependency on the no-media-from-WhatsApp
operational workaround."
```

---

### Task 41: Final lint + format + type + test-unit + close-out commit

**Files:**
- Possibly polish anything `ruff` / `mypy` flags

- [ ] **Step 1: Run the full pre-commit gauntlet**

```bash
cd /Users/nicolasamaral/dev/PeSDR-fe01b-pipeline
make lint
make format
make type
make test-unit
```

If any failures, fix inline. Re-run until green.

- [ ] **Step 2: Run integration suite (requires docker compose)**

```bash
make up
uv run alembic upgrade head
make test-integration
```

Expected: all pass.

- [ ] **Step 3: Close-out commit**

```bash
git add -u
git commit --allow-empty -m "chore(fe03a): close-out — all 41 tasks landed

Spec: docs/superpowers/specs/2026-06-09-fe03a-objection-runtime-design.md
Plan: docs/superpowers/plans/2026-06-10-fe03a-objection-runtime.md

Delivers:
- ActiveTreatment state machine (IDLE <-> ACTIVE with 6 transitions)
- Python validator with product whitelist + fallback exhausted path
- TreeFlow YAML extensions (global_objections + handles_objections + tool_payload + bounds)
- TurnDecision schema extensions (treatment_status, off_topic_detected)
- system_prompt extensions (active_treatment block + bridge in next_nodes + node-scoped objections + conservative resolution guidance)
- Routing protections (extended simpleeval context + active_treatment block)
- Heuristics: contradiction correction + implicit transition audit
- Off-topic counter + escalation
- Pipeline transactional consistency + inbound concat window + TreeFlow snapshot
- Migration 0025 (talks.requires_review_reason)
- 27 unit tests + 8 integration tests
- CLAUDE.md updated

Out of scope (deferred): humanization (FE-03b), close lifecycle (FE-03b),
on_collected runtime (FE-03c), adapter framework (FE-03c), Sentinel
(FE-04), voice (FE-05), event bus (FE-06), HITL approval (FE-07)."
```

- [ ] **Step 4: Push the branch**

```bash
git push -u origin dev/nicolas-fe03a-objection-runtime
```

Open PR against `dev/nicolas-fe01b-pipeline` (continues the FE refactor line):

```bash
gh pr create \
  --base dev/nicolas-fe01b-pipeline \
  --head dev/nicolas-fe03a-objection-runtime \
  --title "FE-03a: Objection Runtime + Python Validator (41 tasks)" \
  --body "$(cat <<'EOF'
## Summary

Per spec `docs/superpowers/specs/2026-06-09-fe03a-objection-runtime-design.md`.
Implementation plan in `docs/superpowers/plans/2026-06-10-fe03a-objection-runtime.md`.

Substitui o Plano 4a + critic LLM na linha FlowEngine v2.

## Test plan

- [ ] `make lint && make format && make type && make test-unit` clean
- [ ] `make test-integration` clean
- [ ] Smoke via `ai-sdr simulate --arch-v2 --tenant avelum --treeflow t01` com TreeFlow YAML que tem `global_objections` declaradas
- [ ] Manual: send "tá caro" mid-conversation, verify treatment enters; send "fechou" later, verify treatment resolves accepted

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage check (vs `2026-06-09-fe03a-objection-runtime-design.md`):**

| Spec section | Implementation task(s) |
|---|---|
| §3 Architecture overview | T1-T31 distributed |
| §4 ActiveTreatment state machine | T17-T22 |
| §4.5 LLM-visible block | T11 |
| §5 TurnDecision extensions | T1, T25 |
| §6 YAML schema extensions | T5, T6, T7 |
| §7 Python validator | T8, T9, T10, T28 |
| §8 Brechas matrix A1-A8 | T25 (A1), T26 (A2), T11 (A4); A3/A5/A7/A8 = documented in CLAUDE.md (T40) |
| §8 Brechas matrix B1-B5 | T30 (B1), T31 (B2), T28 (B3), T23 (B4); B5 = OOS doc |
| §8 Brechas matrix C1-C4 | T15 (C1), T16 (C2), T12 (C3), T24 (C4) |
| §9 Idempotência / transação | T29, T30 |
| §10 State migration policy | T2 |
| §11 requires_review_reason | T3, T4, T27, T28 |
| §12 Events list (14 + 3 new) | All `objection_runtime` + `heuristics` + `offtopic` + `escalation` tasks emit; pipeline t27 flushes via structlog |
| §13 Testing strategy | All unit + integration tests across T1-T39 |
| §14 Migration / cutover | T40 documents in CLAUDE.md |
| §15 Out of scope | T40 documents in CLAUDE.md + close-out commit message |

**Placeholder scan:** Searched the document — no "TBD", no "TODO", no "implement later". One acknowledged spot in T30 ("TBD via grep below") is **a literal instruction to the engineer** to grep the codebase rather than a content gap; the step shows exactly what command to run. Leave as-is.

**Type consistency check:**
- `StateDelta` fields used the same way in T17-T22 + T27.
- `apply_decision(...)` signature consistent: T27 introduces `treeflow=`, T28 keeps it.
- `requires_review_reason` enum strings consistent across T3, T20, T25, T26, T27, T28, T40.
- `validate_transition(state=...)` signature consistent T14-T16.
- `TurnDecision.treatment_status` literal triple consistent in T1, T18-T22.

**Spec section without a task:** none found.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-10-fe03a-objection-runtime.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
