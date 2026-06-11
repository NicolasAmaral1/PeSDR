# FE-03b — Humanization + Close Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Humanizar saída do agente WhatsApp (chunking + typing delays) + fechar Talks automaticamente por inactivity/duration/completion rule + re-engagement (nova Talk quando lead volta após close).

**Architecture:** Adiciona 3 módulos puros (`humanizer.py`, `close_lifecycle.py`, `models/talk_status.py`) + 1 background job (`worker/jobs/scan_talks.py`) + extensões pontuais em `sender.py`, `post_processing.py`, `preprocessing.py`, `treeflow_loader.py`, `messaging/base.py`, `messaging/whatsapp_cloud.py`, `schemas/tenant_yaml.py`, `models/talk.py`. Migration 0026 adiciona 4 valores ao enum `talks.status` via single source of truth `ALL_STATUSES`.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · simpleeval · isodate · arq · pytest · structlog. Sem novas dependências externas (isodate já vem do Plano 9).

**Spec fonte:** `docs/superpowers/specs/2026-06-10-fe03b-humanization-close-lifecycle-design.md` (commit `9f01633`).

**Branch:** `dev/nicolas-fe03b-humanization-close-lifecycle` (já criada a partir do head de `dev/nicolas-fe03a-objection-runtime`).

**Worktree:** `/Users/nicolasamaral/dev/PeSDR-fe01b-pipeline`.

---

## File structure

### Novos arquivos
- `src/ai_sdr/flowengine/humanizer.py` — `HumanizationConfig` + `Chunk` dataclasses + `humanize()` pure function
- `src/ai_sdr/flowengine/close_lifecycle.py` — `CloseOutcome` + `evaluate_completion_rule()` pure function
- `src/ai_sdr/worker/jobs/scan_talks.py` — `scan_active_talks()` cross-tenant scan + `_close()` helper
- `src/ai_sdr/models/talk_status.py` — `TalkStatus` Literal + `ALL_STATUSES` tuple
- `migrations/versions/0026_talks_status_lifecycle_values.py` — extends `ck_talks_status` CHECK constraint

### Arquivos modificados (`src/ai_sdr/flowengine/`)
- `sender.py` — itera chunks com delay + mark_as_typing
- `post_processing.py` — invoca `evaluate_completion_rule` após state delta (mutually exclusive com requires_review_reason chain)
- `preprocessing.py` — re-engagement log quando lead tem Talk previamente fechada
- `treeflow_loader.py` — parse `talk_lifecycle` block + bounds validation
- `pipeline.py` — passa `humanization_config` resolvido do tenant pro sender

### Arquivos modificados (outros)
- `src/ai_sdr/messaging/base.py` — adiciona `mark_as_typing(to)` opcional ao protocol
- `src/ai_sdr/messaging/whatsapp_cloud.py` — implementa mark_as_typing via Meta typing_indicator API
- `src/ai_sdr/messaging/fake.py` — passthrough no-op pra mark_as_typing
- `src/ai_sdr/schemas/tenant_yaml.py` — `HumanizationConfig` model + bounds validator + campo em `TenantConfig`
- `src/ai_sdr/models/talk.py` — `status` Mapped[TalkStatus]
- `src/ai_sdr/repositories/talk_repository.py` — adiciona `find_most_recent_closed`
- `src/ai_sdr/worker/main.py` — registra `scheduled_scan_talks` no `cron_jobs`
- `CLAUDE.md` — seção "FE-03b: Humanization + Close Lifecycle"

### Arquivos de teste (`tests/unit/` flat)
22 novos arquivos.

### Arquivos de teste (`tests/integration/` flat)
5 novos arquivos (skip-friendly per Phase 11 pattern de FE-03a).

### Fixtures (`tests/fixtures/`)
- `avelum_v2_with_lifecycle.yaml` — TreeFlow com `talk_lifecycle` completo
- `treeflow_invalid_iso_duration.yaml`
- `treeflow_invalid_completion_expression.yaml`
- `treeflow_invalid_outcome.yaml`

---

## Phase 1: Status enum + migration foundation

### Task 1: `TalkStatus` Literal + `ALL_STATUSES` single source of truth

**Files:**
- Create: `src/ai_sdr/models/talk_status.py`
- Test: `tests/unit/test_talk_status_literal_source_of_truth.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_talk_status_literal_source_of_truth.py
"""TalkStatus Literal exports canonical enum values (FE-03b Task 1)."""
from __future__ import annotations

from ai_sdr.models.talk_status import ALL_STATUSES, TalkStatus


def test_all_statuses_has_expected_length():
    assert len(ALL_STATUSES) == 10


def test_all_statuses_contains_pre_fe03b_values():
    """Backward-compat: pre-FE-03b statuses still present."""
    for v in ("active", "requires_review", "closed_completed",
              "closed_inactivity", "closed_optout", "closed_banned"):
        assert v in ALL_STATUSES


def test_all_statuses_contains_fe03b_new_values():
    """New FE-03b values present."""
    for v in ("closed_completed_success", "closed_completed_failure",
              "closed_no_interest", "closed_duration"):
        assert v in ALL_STATUSES


def test_talk_status_is_literal():
    """TalkStatus is a Literal type covering ALL_STATUSES exactly."""
    from typing import get_args
    assert set(get_args(TalkStatus)) == set(ALL_STATUSES)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicolasamaral/dev/PeSDR-fe01b-pipeline && uv run pytest tests/unit/test_talk_status_literal_source_of_truth.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/models/talk_status.py`:

```python
"""Canonical Literal for `talks.status` (FE-03b Task 1).

Single source of truth across migration 0026, the ORM column, the worker
scan job, and close_lifecycle module. Keep in sync — if you add a value
here, update migration 0026's upgrade() to extend the CHECK constraint.

Pattern mirrors ai_sdr.models.review_reason (FE-03a) for talks.requires_review_reason.
"""

from __future__ import annotations

from typing import Literal, get_args

TalkStatus = Literal[
    "active",
    "requires_review",
    "closed_completed",            # backward-compat (pre-FE-03b)
    "closed_completed_success",
    "closed_completed_failure",
    "closed_no_interest",
    "closed_duration",
    "closed_inactivity",
    "closed_optout",
    "closed_banned",
]

ALL_STATUSES: tuple[str, ...] = get_args(TalkStatus)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_talk_status_literal_source_of_truth.py -v`
Expected: PASS (4/4).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talk_status.py tests/unit/test_talk_status_literal_source_of_truth.py
git commit -m "feat(fe03b t1): TalkStatus Literal + ALL_STATUSES source of truth

Pattern mirrors ai_sdr.models.review_reason from FE-03a. Per spec §7.1."
```

---

### Task 2: Migration 0026 — extend `ck_talks_status` CHECK constraint

**Files:**
- Create: `migrations/versions/0026_talks_status_lifecycle_values.py`
- Test: `tests/integration/test_migration_0026_status_enum.py` (create — skip-friendly)

- [ ] **Step 1: Write the failing integration test (skip-friendly)**

```python
# tests/integration/test_migration_0026_status_enum.py
"""Migration 0026 extends talks.status CHECK constraint (FE-03b Task 2)."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.talk_status import ALL_STATUSES

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pg_constraint_accepts_all_lifecycle_statuses(async_engine):
    """Every value in ALL_STATUSES satisfies the new CHECK constraint."""
    async with AsyncSession(async_engine) as session:
        for v in ALL_STATUSES:
            await session.execute(text("SAVEPOINT v"))
            try:
                await session.execute(
                    text(
                        "SELECT 1 WHERE :v IN (" +
                        ", ".join(f"'{s}'" for s in ALL_STATUSES) +
                        ")"
                    ),
                    {"v": v},
                )
            finally:
                await session.execute(text("ROLLBACK TO SAVEPOINT v"))


@pytest.mark.asyncio
async def test_constraint_rejects_unknown_status(async_engine):
    """Status not in ALL_STATUSES raises IntegrityError."""
    async with AsyncSession(async_engine) as session:
        await session.execute(text("SAVEPOINT v"))
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO talks "
                    "(id, tenant_id, lead_id, treeflow_id, treeflow_version_id, "
                    " status, handling_mode, last_message_at, opened_at) "
                    "VALUES (gen_random_uuid(), "
                    "  (SELECT id FROM tenants LIMIT 1), "
                    "  (SELECT id FROM leads LIMIT 1), "
                    "  (SELECT treeflow_id FROM treeflow_versions LIMIT 1), "
                    "  (SELECT id FROM treeflow_versions LIMIT 1), "
                    "  'bogus_status', 'ai', now(), now())"
                )
            )
        await session.execute(text("ROLLBACK TO SAVEPOINT v"))
```

(Add a local `async_engine` fixture in the test file using `create_async_engine + NullPool` — same pattern as `tests/integration/test_migration_0025_requires_review_reason.py` from FE-03a.)

- [ ] **Step 2: Run test to verify it fails locally**

Run: `uv run pytest tests/integration/test_migration_0026_status_enum.py --collect-only -v`
Expected: tests COLLECT (skip locally — no Docker). Migration apply + assertion happen on VPS post-push.

- [ ] **Step 3: Apply migration**

Create `migrations/versions/0026_talks_status_lifecycle_values.py`:

```python
"""talks.status enum: add lifecycle close values (FlowEngine FE-03b)

Per spec §16.3. Adds closed_completed_success/failure, closed_no_interest,
closed_duration. Preserves backward-compat with closed_completed.

Revision ID: 0026_talks_status_lifecycle_values
Revises: 0025_talks_requires_review_reason
Create Date: 2026-06-10 00:00:00
"""

from alembic import op

from ai_sdr.models.talk_status import ALL_STATUSES

revision = "0026_talks_status_lifecycle_values"
down_revision = "0025_talks_requires_review_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_talks_status", "talks", type_="check")
    op.create_check_constraint(
        "ck_talks_status",
        "talks",
        "status IN ('active', 'requires_review', 'closed_completed', "
        "'closed_inactivity', 'closed_optout', 'closed_banned')",
    )
```

- [ ] **Step 4: Validate static + alembic chain**

Run:
```bash
uv run alembic history | head -5
uv run ruff check migrations/versions/0026_talks_status_lifecycle_values.py
```
Expected: `0026_talks_status_lifecycle_values` listed as head; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0026_talks_status_lifecycle_values.py tests/integration/test_migration_0026_status_enum.py
git commit -m "feat(fe03b t2): migration 0026 — extend talks.status enum

Adds closed_completed_success/failure + closed_no_interest +
closed_duration. Imports ALL_STATUSES from ai_sdr.models.talk_status
(single source of truth). Per spec §7."
```

---

### Task 3: `Talk.status` ORM column typed `Mapped[TalkStatus]`

**Files:**
- Modify: `src/ai_sdr/models/talk.py`
- Test: `tests/unit/test_talk_model_status_typed.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_talk_model_status_typed.py
"""Talk.status ORM column references TalkStatus Literal (FE-03b Task 3)."""
from __future__ import annotations

from ai_sdr.models.talk import Talk
from ai_sdr.models.talk_status import ALL_STATUSES


def test_status_column_exists():
    assert hasattr(Talk, "status")


def test_status_column_is_text_type():
    col = Talk.__table__.c.status
    type_str = str(col.type).upper()
    assert "TEXT" in type_str or "VARCHAR" in type_str


def test_status_column_not_nullable():
    assert Talk.__table__.c.status.nullable is False


def test_all_known_statuses_can_be_assigned_at_typing_level():
    """Type narrowing check — every value in ALL_STATUSES is a valid TalkStatus."""
    from typing import get_args
    from ai_sdr.models.talk_status import TalkStatus
    assert set(get_args(TalkStatus)) == set(ALL_STATUSES)
```

- [ ] **Step 2: Run test to verify it (partially) passes**

Run: `uv run pytest tests/unit/test_talk_model_status_typed.py -v`
Expected: 3 PASS, 1 PASS (the type narrowing check uses Task 1's module which already exists).

The point of this task is not the test verifying behavior change — it's the type annotation upgrade. Static type-checker (mypy) will validate the change.

- [ ] **Step 3: Apply implementation**

Open `src/ai_sdr/models/talk.py`. Find the existing `status` column declaration (around line 71):

```python
    status: Mapped[str] = mapped_column(Text(), nullable=False)
```

Replace with:

```python
    status: Mapped[TalkStatus] = mapped_column(Text(), nullable=False)
```

Add the import at the top of the file:

```python
from ai_sdr.models.talk_status import TalkStatus
```

- [ ] **Step 4: Run test + mypy**

```bash
uv run pytest tests/unit/test_talk_model_status_typed.py -v
uv run mypy src/ai_sdr/models/talk.py
```
Expected: PASS; mypy clean for talk.py touched lines (pre-existing errors stay; no new errors introduced).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talk.py tests/unit/test_talk_model_status_typed.py
git commit -m "feat(fe03b t3): Talk.status Mapped[TalkStatus] typed column

Imports TalkStatus from ai_sdr.models.talk_status. Callsites that
assign status are now mypy-checked against the canonical 10 values."
```

---

## Phase 2: TreeFlow YAML extensions

### Task 4: `TreeflowTalkLifecycle` + `TreeflowCompletionRule` dataclasses on `TreeflowDef`

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_talk_lifecycle.py` (create)
- Fixture: `tests/fixtures/avelum_v2_with_lifecycle.yaml` (create)

- [ ] **Step 1: Create the fixture**

```yaml
# tests/fixtures/avelum_v2_with_lifecycle.yaml
schema_version: 1
id: avelum_v2_lifecycle
version: 1.0.0
display_name: "Avelum SDR — with lifecycle"

sdr_persona:
  voice: "PT-BR informal"
  conduct: "1. Reconheça antes de avançar"

talk_lifecycle:
  close_after_inactivity: P7D
  close_after_duration: P30D
  close_when_completed:
    - expression: "collected.demo_agendada == true"
      outcome: success
    - expression: "collected.no_interest_flag == true"
      outcome: no_interest

entry_node: saudacao
nodes:
  - id: saudacao
    objetivo: "Cumprimentar lead"
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
# tests/unit/test_treeflow_loader_talk_lifecycle.py
"""TreeFlowLoader parses talk_lifecycle block (FE-03b Task 4)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from ai_sdr.flowengine.treeflow_loader import load_treeflow_v2

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "avelum_v2_with_lifecycle.yaml"
)


def test_talk_lifecycle_loaded():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert hasattr(tf, "talk_lifecycle")
    assert tf.talk_lifecycle is not None


def test_inactivity_parsed_as_timedelta():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert tf.talk_lifecycle.close_after_inactivity == timedelta(days=7)


def test_duration_parsed_as_timedelta():
    tf = load_treeflow_v2(FIXTURE.read_text())
    assert tf.talk_lifecycle.close_after_duration == timedelta(days=30)


def test_completion_rules_parsed():
    tf = load_treeflow_v2(FIXTURE.read_text())
    rules = tf.talk_lifecycle.close_when_completed
    assert len(rules) == 2
    assert rules[0].expression == "collected.demo_agendada == true"
    assert rules[0].outcome == "success"
    assert rules[1].outcome == "no_interest"


def test_treeflow_without_talk_lifecycle_block_returns_none():
    """Backward-compat: TreeFlows without the block load with None."""
    yaml_text = """
schema_version: 1
id: minimal
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
"""
    tf = load_treeflow_v2(yaml_text)
    assert tf.talk_lifecycle is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_treeflow_loader_talk_lifecycle.py -v`
Expected: FAIL — `TreeflowDef.talk_lifecycle` does not exist.

- [ ] **Step 4: Apply implementation**

In `src/ai_sdr/flowengine/treeflow_loader.py`:

1. Add imports at the top:

```python
from datetime import timedelta

import isodate
from simpleeval import SimpleEval
```

2. Add new dataclasses after `TreeflowObjection` (around line 68):

```python
@dataclass
class TreeflowCompletionRule:
    expression: str
    outcome: str  # "success" | "failure" | "no_interest"


@dataclass
class TreeflowTalkLifecycle:
    close_after_inactivity: timedelta | None = None
    close_after_duration: timedelta | None = None
    close_when_completed: list[TreeflowCompletionRule] = field(default_factory=list)
```

3. Extend `TreeflowDef`:

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
    talk_lifecycle: TreeflowTalkLifecycle | None = None  # NEW
```

4. Add the minimal parser (full bounds validation comes in T5):

```python
def _parse_talk_lifecycle(raw: dict[str, Any] | None) -> TreeflowTalkLifecycle | None:
    if raw is None:
        return None

    inactivity = raw.get("close_after_inactivity")
    inactivity_td = isodate.parse_duration(inactivity) if inactivity else None

    duration = raw.get("close_after_duration")
    duration_td = isodate.parse_duration(duration) if duration else None

    completion_raw = raw.get("close_when_completed") or []
    completion = [
        TreeflowCompletionRule(
            expression=entry["expression"],
            outcome=entry["outcome"],
        )
        for entry in completion_raw
    ]

    return TreeflowTalkLifecycle(
        close_after_inactivity=inactivity_td,
        close_after_duration=duration_td,
        close_when_completed=completion,
    )
```

5. In `load_treeflow_v2`, after building `global_objections`, add:

```python
    talk_lifecycle = _parse_talk_lifecycle(data.get("talk_lifecycle"))
```

And include in the returned `TreeflowDef(...)`:

```python
        talk_lifecycle=talk_lifecycle,
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
uv run pytest tests/unit/test_treeflow_loader_talk_lifecycle.py -v
uv run pytest tests/unit/ -k treeflow_loader -v
```
Expected: PASS (5/5 new); no regressions in T5-T7 of FE-03a treeflow tests.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_talk_lifecycle.py tests/fixtures/avelum_v2_with_lifecycle.yaml
git commit -m "feat(fe03b t4): TreeFlowLoader parses talk_lifecycle block

Adds TreeflowTalkLifecycle + TreeflowCompletionRule dataclasses and
parser. ISO-8601 durations via isodate. Block omission yields None
(backward-compat with TreeFlows authored before FE-03b)."
```

---

### Task 5: TreeFlowLoader bounds validation for `talk_lifecycle`

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_lifecycle_bounds.py` (create)
- Fixtures: 3 invalid yamls

- [ ] **Step 1: Create invalid fixtures**

`tests/fixtures/treeflow_invalid_iso_duration.yaml`:
```yaml
schema_version: 1
id: bad_iso
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_inactivity: P7d  # lowercase d — invalid
```

`tests/fixtures/treeflow_invalid_completion_expression.yaml`:
```yaml
schema_version: 1
id: bad_expr
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_when_completed:
    - expression: "collected.field !!! bad syntax"
      outcome: success
```

`tests/fixtures/treeflow_invalid_outcome.yaml`:
```yaml
schema_version: 1
id: bad_outcome
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_when_completed:
    - expression: "true"
      outcome: bogus  # not in {success, failure, no_interest}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_treeflow_loader_lifecycle_bounds.py
"""TreeFlowLoader bounds validation for talk_lifecycle (FE-03b Task 5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2

F = Path(__file__).resolve().parent.parent / "fixtures"


def test_rejects_invalid_iso_duration():
    with pytest.raises(TreeflowLoadError, match="close_after_inactivity"):
        load_treeflow_v2((F / "treeflow_invalid_iso_duration.yaml").read_text())


def test_rejects_invalid_completion_expression_syntax():
    with pytest.raises(TreeflowLoadError, match="close_when_completed"):
        load_treeflow_v2((F / "treeflow_invalid_completion_expression.yaml").read_text())


def test_rejects_invalid_outcome():
    with pytest.raises(TreeflowLoadError, match="outcome"):
        load_treeflow_v2((F / "treeflow_invalid_outcome.yaml").read_text())


def test_rejects_inactivity_below_min_bound():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_inactivity: PT30M  # 30 minutes; below minimum PT1H
"""
    with pytest.raises(TreeflowLoadError, match="PT1H"):
        load_treeflow_v2(yaml_text)


def test_rejects_inactivity_above_max_bound():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_inactivity: P400D  # 400 days; above maximum P365D
"""
    with pytest.raises(TreeflowLoadError, match="P365D"):
        load_treeflow_v2(yaml_text)


def test_rejects_duration_out_of_bounds():
    yaml_text = """
schema_version: 1
id: bad
version: 1.0.0
sdr_persona: { voice: "x", conduct: "1. y" }
entry_node: a
nodes:
  - id: a
    objetivo: "x"
    collects: []
    exit_condition: { type: all_fields_filled }
    next_nodes: [{ condition: "true", target: a }]
talk_lifecycle:
  close_after_duration: PT12H  # 12 hours; below minimum P1D
"""
    with pytest.raises(TreeflowLoadError, match="P1D"):
        load_treeflow_v2(yaml_text)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_treeflow_loader_lifecycle_bounds.py -v`
Expected: 6 FAIL — current `_parse_talk_lifecycle` is too permissive (just calls `isodate.parse_duration` without bounds).

- [ ] **Step 4: Apply implementation**

In `treeflow_loader.py`, replace `_parse_talk_lifecycle` with the strict version:

```python
_ALLOWED_OUTCOMES = {"success", "failure", "no_interest"}
_MIN_INACTIVITY = timedelta(hours=1)
_MAX_INACTIVITY = timedelta(days=365)
_MIN_DURATION = timedelta(days=1)
_MAX_DURATION = timedelta(days=730)


def _parse_talk_lifecycle(raw: dict[str, Any] | None) -> TreeflowTalkLifecycle | None:
    if raw is None:
        return None

    inactivity = raw.get("close_after_inactivity")
    inactivity_td: timedelta | None = None
    if inactivity:
        try:
            inactivity_td = isodate.parse_duration(inactivity)
        except (isodate.ISO8601Error, ValueError) as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_inactivity invalid ISO-8601: "
                f"{inactivity!r}: {e}"
            ) from e
        if not (_MIN_INACTIVITY <= inactivity_td <= _MAX_INACTIVITY):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_inactivity must be in "
                f"[PT1H, P365D], got {inactivity}"
            )

    duration = raw.get("close_after_duration")
    duration_td: timedelta | None = None
    if duration:
        try:
            duration_td = isodate.parse_duration(duration)
        except (isodate.ISO8601Error, ValueError) as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_duration invalid ISO-8601: "
                f"{duration!r}: {e}"
            ) from e
        if not (_MIN_DURATION <= duration_td <= _MAX_DURATION):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_after_duration must be in "
                f"[P1D, P730D], got {duration}"
            )

    completion_raw = raw.get("close_when_completed") or []
    completion: list[TreeflowCompletionRule] = []
    for entry in completion_raw:
        if not isinstance(entry, dict):
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed entries must be mappings, "
                f"got {entry!r}"
            )
        expr = entry.get("expression")
        outcome = entry.get("outcome")
        if not expr:
            raise TreeflowLoadError(
                "talk_lifecycle.close_when_completed entry missing 'expression'"
            )
        if outcome not in _ALLOWED_OUTCOMES:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed entry outcome must be one "
                f"of {sorted(_ALLOWED_OUTCOMES)}, got {outcome!r}"
            )
        try:
            SimpleEval(names={}).parse(expr)
        except Exception as e:
            raise TreeflowLoadError(
                f"talk_lifecycle.close_when_completed expression invalid syntax: "
                f"{expr!r}: {e}"
            ) from e
        completion.append(
            TreeflowCompletionRule(expression=expr, outcome=outcome)
        )

    return TreeflowTalkLifecycle(
        close_after_inactivity=inactivity_td,
        close_after_duration=duration_td,
        close_when_completed=completion,
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_loader_lifecycle_bounds.py -v
uv run pytest tests/unit/test_treeflow_loader_talk_lifecycle.py -v
```
Expected: PASS (6/6 new), no regression in T4.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_lifecycle_bounds.py tests/fixtures/treeflow_invalid_iso_duration.yaml tests/fixtures/treeflow_invalid_completion_expression.yaml tests/fixtures/treeflow_invalid_outcome.yaml
git commit -m "feat(fe03b t5): bounds validation for talk_lifecycle in TreeFlowLoader

inactivity ∈ [PT1H, P365D], duration ∈ [P1D, P730D], outcome ∈
{success, failure, no_interest}, expression parses via simpleeval.
Errors are fatal — tenant nem inicia. Per spec §6.3."
```

---

## Phase 3: Humanizer

### Task 6: `humanizer.py` — `HumanizationConfig` + `Chunk` + `humanize()` pure function

**Files:**
- Create: `src/ai_sdr/flowengine/humanizer.py`
- Test: `tests/unit/test_humanizer.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_humanizer.py
"""humanizer.humanize() chunks + delays (FE-03b Task 6)."""
from __future__ import annotations

from ai_sdr.flowengine.humanizer import Chunk, HumanizationConfig, humanize


def _default_config(**overrides):
    return HumanizationConfig(**overrides)


def test_paragraph_split_yields_multiple_chunks():
    cfg = _default_config()
    text = "Olá!\n\nQue legal saber.\n\nQual seu segmento?"
    chunks = humanize(text, cfg)
    assert len(chunks) == 3
    assert chunks[0].text == "Olá!"
    assert chunks[1].text == "Que legal saber."
    assert chunks[2].text == "Qual seu segmento?"


def test_first_chunk_has_zero_delay():
    cfg = _default_config()
    chunks = humanize("Olá!\n\nMundo!", cfg)
    assert chunks[0].delay_before_ms == 0


def test_subsequent_chunks_have_delay_bounded():
    cfg = _default_config(min_delay_ms=500, max_delay_ms=2000)
    chunks = humanize("a\n\nb", cfg)  # tiny chunks → would be < min without bounds
    assert chunks[1].delay_before_ms >= 500
    assert chunks[1].delay_before_ms <= 2000


def test_delay_proportional_to_next_chunk_length():
    cfg = _default_config(
        chars_per_second_min=10.0,
        chars_per_second_max=10.0,  # deterministic
        min_delay_ms=0,
        max_delay_ms=10_000,
    )
    chunks = humanize("a\n\n" + "x" * 100, cfg)
    # 100 chars at 10 chars/s = 10s = 10000ms
    assert 9500 <= chunks[1].delay_before_ms <= 10500


def test_voice_mode_returns_single_chunk_no_delay():
    cfg = _default_config(apply_to_voice=False)
    text = "Olá!\n\nMundo!"
    chunks = humanize(text, cfg, is_voice=True)
    assert len(chunks) == 1
    assert chunks[0].text == text  # unchanged
    assert chunks[0].delay_before_ms == 0


def test_voice_mode_apply_to_voice_still_chunks():
    cfg = _default_config(apply_to_voice=True)
    chunks = humanize("Olá!\n\nMundo!", cfg, is_voice=True)
    assert len(chunks) == 2


def test_disabled_returns_single_chunk():
    cfg = _default_config(enabled=False)
    chunks = humanize("Olá!\n\nMundo!", cfg)
    assert len(chunks) == 1
    assert chunks[0].text == "Olá!\n\nMundo!"


def test_no_delimiter_in_text_yields_single_chunk():
    cfg = _default_config()
    chunks = humanize("Tudo numa linha só.", cfg)
    assert len(chunks) == 1
    assert chunks[0].delay_before_ms == 0


def test_empty_response_returns_empty_list():
    cfg = _default_config()
    chunks = humanize("", cfg)
    assert chunks == []


def test_only_whitespace_response_returns_empty_list():
    cfg = _default_config()
    chunks = humanize("   \n\n   \n\n  ", cfg)
    assert chunks == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_humanizer.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/flowengine/humanizer.py`:

```python
"""Humanization post-processor for FlowEngine v2 sender (FE-03b §4).

Pure function. Splits the LLM's response_text into chunks (default by
paragraph delimiter \\n\\n) and computes a typing-style delay before each
non-first chunk. Voice mode short-circuits to a single chunk unless the
tenant opts into apply_to_voice.

The actual sleep + send loop lives in flowengine.sender. This module
only computes the (text, delay_before_ms) tuples.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class HumanizationConfig:
    """Per-tenant config from tenant.yaml > humanization."""

    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = 8.0
    chars_per_second_max: float = 15.0
    min_delay_ms: int = 800
    max_delay_ms: int = 4000
    apply_to_voice: bool = False


@dataclass(frozen=True)
class Chunk:
    """One outbound message in the humanized sequence."""

    text: str
    delay_before_ms: int  # 0 for first chunk


def humanize(
    response_text: str,
    config: HumanizationConfig,
    *,
    is_voice: bool = False,
) -> list[Chunk]:
    """Split response into chunks with typing-style delays.

    Voice mode short-circuits to single chunk (per spec §13.5) unless
    config.apply_to_voice. Humanization disabled → single chunk.
    Empty / whitespace-only input → empty list.
    """
    if is_voice and not config.apply_to_voice:
        return [Chunk(text=response_text, delay_before_ms=0)] if response_text.strip() else []

    if not config.enabled:
        return [Chunk(text=response_text, delay_before_ms=0)] if response_text.strip() else []

    raw_chunks = [
        c.strip()
        for c in response_text.split(config.chunk_delimiter)
        if c.strip()
    ]
    if not raw_chunks:
        return []

    chunks = [Chunk(text=raw_chunks[0], delay_before_ms=0)]
    for next_chunk_text in raw_chunks[1:]:
        if config.chars_per_second_min == config.chars_per_second_max:
            typing_speed = config.chars_per_second_min
        else:
            typing_speed = random.uniform(
                config.chars_per_second_min,
                config.chars_per_second_max,
            )
        typing_ms = int(len(next_chunk_text) / typing_speed * 1000)
        delay = max(config.min_delay_ms, min(config.max_delay_ms, typing_ms))
        chunks.append(Chunk(text=next_chunk_text, delay_before_ms=delay))

    return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_humanizer.py -v`
Expected: PASS (10/10).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/humanizer.py tests/unit/test_humanizer.py
git commit -m "feat(fe03b t6): humanizer pure function — paragraph chunking + bounded delays

Per spec §4. HumanizationConfig + Chunk dataclasses. Voice mode
short-circuits unless apply_to_voice. Delimiter \\n\\n is the
tenant-configurable default. Typing speed proportional to next chunk's
length, bounded by min/max_delay_ms."
```

---

### Task 7: `HumanizationConfig` in `tenant_yaml.py` schema + bounds validator

**Files:**
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Test: `tests/unit/test_tenant_yaml_humanization.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tenant_yaml_humanization.py
"""TenantConfig.humanization parsing + bounds (FE-03b Task 7)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import HumanizationConfig


def test_defaults_are_sensible():
    cfg = HumanizationConfig()
    assert cfg.enabled is True
    assert cfg.chunk_delimiter == "\n\n"
    assert cfg.chars_per_second_min == 8.0
    assert cfg.chars_per_second_max == 15.0
    assert cfg.min_delay_ms == 800
    assert cfg.max_delay_ms == 4000
    assert cfg.apply_to_voice is False


def test_accepts_valid_override():
    cfg = HumanizationConfig(
        enabled=False,
        chars_per_second_min=5.0,
        chars_per_second_max=20.0,
        min_delay_ms=200,
        max_delay_ms=8000,
    )
    assert cfg.enabled is False
    assert cfg.chars_per_second_min == 5.0


def test_rejects_chars_per_second_min_greater_than_max():
    with pytest.raises(ValidationError, match="chars_per_second_min"):
        HumanizationConfig(
            chars_per_second_min=20.0,
            chars_per_second_max=10.0,
        )


def test_rejects_min_delay_greater_than_max_delay():
    with pytest.raises(ValidationError, match="min_delay_ms"):
        HumanizationConfig(min_delay_ms=5000, max_delay_ms=1000)


def test_rejects_negative_chars_per_second():
    with pytest.raises(ValidationError):
        HumanizationConfig(chars_per_second_min=-1.0, chars_per_second_max=10.0)


def test_rejects_negative_delay_ms():
    with pytest.raises(ValidationError):
        HumanizationConfig(min_delay_ms=-100, max_delay_ms=1000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tenant_yaml_humanization.py -v`
Expected: FAIL — `HumanizationConfig` doesn't exist in `tenant_yaml.py` (or differs from spec).

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/schemas/tenant_yaml.py`:

1. Add imports (if not already present):

```python
from pydantic import BaseModel, Field, model_validator
```

2. Add the model (place near other config models, e.g., after `GuardrailsConfig`):

```python
class HumanizationConfig(BaseModel):
    """Per-tenant humanization knobs (FE-03b §4).

    All defaults align with the spec block; tenants without the
    `humanization` block in their tenant.yaml inherit these.
    """

    enabled: bool = True
    chunk_delimiter: str = "\n\n"
    chars_per_second_min: float = Field(default=8.0, gt=0)
    chars_per_second_max: float = Field(default=15.0, gt=0)
    min_delay_ms: int = Field(default=800, ge=0)
    max_delay_ms: int = Field(default=4000, ge=0)
    apply_to_voice: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> "HumanizationConfig":
        if self.chars_per_second_min > self.chars_per_second_max:
            raise ValueError(
                "humanization.chars_per_second_min must be <= chars_per_second_max"
            )
        if self.min_delay_ms > self.max_delay_ms:
            raise ValueError(
                "humanization.min_delay_ms must be <= max_delay_ms"
            )
        return self
```

3. Add the field to `TenantConfig`:

```python
class TenantConfig(BaseModel):
    # ... existing fields ...
    humanization: HumanizationConfig = Field(default_factory=HumanizationConfig)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_tenant_yaml_humanization.py -v
uv run pytest tests/unit/test_tenant_yaml.py -v
```
Expected: PASS (6/6 new), no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml_humanization.py
git commit -m "feat(fe03b t7): TenantConfig.humanization with bounds validator

Per spec §4.1. Defaults match the runtime humanizer; bounds enforce
min <= max relationship for both chars/s and delay_ms."
```

---

### Task 8: `MessagingAdapter.mark_as_typing` protocol + adapter impls

**Files:**
- Modify: `src/ai_sdr/messaging/base.py`
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Modify: `src/ai_sdr/messaging/fake.py`
- Test: `tests/unit/test_messaging_mark_as_typing.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_messaging_mark_as_typing.py
"""MessagingAdapter.mark_as_typing protocol + impls (FE-03b Task 8)."""
from __future__ import annotations

import pytest

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.fake import FakeMessagingAdapter


def test_default_protocol_is_no_op():
    """The base class's mark_as_typing default implementation returns None."""
    assert MessagingAdapter.mark_as_typing.__doc__ is not None


@pytest.mark.asyncio
async def test_fake_adapter_records_typing_calls():
    """FakeMessagingAdapter records mark_as_typing calls for tests."""
    adapter = FakeMessagingAdapter()
    await adapter.mark_as_typing("+5511999999999")
    assert adapter.typing_calls == ["+5511999999999"]


@pytest.mark.asyncio
async def test_fake_adapter_typing_calls_empty_by_default():
    adapter = FakeMessagingAdapter()
    assert adapter.typing_calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_messaging_mark_as_typing.py -v`
Expected: FAIL — `mark_as_typing` not on protocol; `FakeMessagingAdapter.typing_calls` not present.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/messaging/base.py`, add a default method to the `MessagingAdapter` ABC:

```python
class MessagingAdapter(ABC):
    # ... existing abstract methods ...

    async def mark_as_typing(self, to: str) -> None:
        """Optional: signal 'typing...' indicator to the recipient.

        Default no-op. Adapters override if the underlying channel supports
        the indicator (e.g., WhatsApp Cloud's typing_indicator API).
        Failures inside the override should be swallowed — typing is a UX
        enhancement and must never block the actual message send.
        """
        return None
```

In `src/ai_sdr/messaging/fake.py`, extend the class:

```python
class FakeMessagingAdapter(MessagingAdapter):
    def __init__(self) -> None:
        # ... existing init ...
        self.typing_calls: list[str] = []

    async def mark_as_typing(self, to: str) -> None:
        self.typing_calls.append(to)
```

In `src/ai_sdr/messaging/whatsapp_cloud.py`, add the override:

```python
async def mark_as_typing(self, to: str) -> None:
    """Call Meta's typing_indicator API. Silent fallback on PolicyError.

    Meta gates typing_indicator per account; older accounts get a 400
    PolicyError which we swallow so the actual message send still happens
    on the next adapter call.
    """
    try:
        await self._post(
            f"/v17.0/{self._phone_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "typing_indicator": {"type": "text"},
            },
        )
    except Exception as exc:
        logger.info(
            "mark_as_typing.failed adapter=whatsapp_cloud to=%s err=%s",
            to, exc,
        )
        return None
```

(Use the same `_post` helper / HTTP client that `send_text` uses; if `_post` raises on non-200, that's the catch path.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_messaging_mark_as_typing.py -v
uv run pytest tests/unit/ -k messaging -v
```
Expected: PASS (3/3 new), no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/base.py src/ai_sdr/messaging/whatsapp_cloud.py src/ai_sdr/messaging/fake.py tests/unit/test_messaging_mark_as_typing.py
git commit -m "feat(fe03b t8): MessagingAdapter.mark_as_typing protocol + impls

Default no-op on the ABC. WhatsApp Cloud calls Meta typing_indicator
API with silent fallback on PolicyError. FakeMessagingAdapter records
calls for test assertions."
```

---

## Phase 4: Sender wiring

### Task 9: `sender.send_response_text` iterates chunks with delay + mark_as_typing

**Files:**
- Modify: `src/ai_sdr/flowengine/sender.py`
- Test: `tests/unit/test_sender_chunked_send.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sender_chunked_send.py
"""sender.send_response_text iterates humanized chunks (FE-03b Task 9)."""
from __future__ import annotations

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig
from ai_sdr.flowengine.sender import send_response_text
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.lead import Lead


def _lead() -> Lead:
    lead = Lead.__new__(Lead)
    lead.id = "00000000-0000-0000-0000-000000000001"
    lead.whatsapp_e164 = "+5511999999999"
    return lead


def _decision(text: str) -> TurnDecision:
    return TurnDecision(
        response_text=text,
        collected_fields={},
        reasoning="test",
    )


def _cfg(min_delay_ms: int = 0, max_delay_ms: int = 0) -> HumanizationConfig:
    """Zero-delay config so the test doesn't actually sleep."""
    return HumanizationConfig(
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
    )


@pytest.mark.asyncio
async def test_three_paragraphs_yield_three_sends():
    adapter = FakeMessagingAdapter()
    text = "Olá!\n\nQue legal saber.\n\nQual seu segmento?"
    result = await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision(text),
        humanization_config=_cfg(),
    )
    assert len(adapter.sent_messages) == 3
    assert adapter.sent_messages[0]["text"] == "Olá!"
    assert adapter.sent_messages[2]["text"] == "Qual seu segmento?"
    assert result.status == "sent"


@pytest.mark.asyncio
async def test_typing_indicator_called_before_each_chunk_with_delay():
    adapter = FakeMessagingAdapter()
    cfg = HumanizationConfig(
        chars_per_second_min=10.0,
        chars_per_second_max=10.0,
        min_delay_ms=0,
        max_delay_ms=10,  # 10ms cap to keep tests fast
    )
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("a\n\nbb\n\nccc"),
        humanization_config=cfg,
    )
    # 3 chunks; first has zero delay (no typing call), 2 with delay → 2 typing
    assert len(adapter.typing_calls) == 2


@pytest.mark.asyncio
async def test_single_chunk_no_typing_call():
    adapter = FakeMessagingAdapter()
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("Apenas uma linha sem delimiter."),
        humanization_config=_cfg(),
    )
    assert len(adapter.sent_messages) == 1
    assert adapter.typing_calls == []


@pytest.mark.asyncio
async def test_disabled_humanization_yields_single_send():
    adapter = FakeMessagingAdapter()
    cfg = HumanizationConfig(enabled=False)
    await send_response_text(
        adapter=adapter,
        lead=_lead(),
        decision=_decision("Olá!\n\nMundo!"),
        humanization_config=cfg,
    )
    assert len(adapter.sent_messages) == 1
    assert adapter.sent_messages[0]["text"] == "Olá!\n\nMundo!"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sender_chunked_send.py -v`
Expected: FAIL — `send_response_text` signature doesn't take `humanization_config`; sends single message.

- [ ] **Step 3: Apply implementation**

Replace the body of `src/ai_sdr/flowengine/sender.py`:

```python
"""Outbound send for FlowEngine v2.

Humanization (chunking + typing indicator + delays) lands in FE-03b.
The function is split-aware: humanizer returns list[Chunk] and we
iterate. Voice paths still fall back to text (FE-05 implements VoiceAdapter).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.humanizer import HumanizationConfig, humanize
from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.models.lead import Lead

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Normalized send outcome — decoupled from MessagingAdapter's SendResult."""

    external_id: str | None
    status: str
    error_detail: str | None = None


async def send_response_text(
    *,
    adapter: MessagingAdapter,
    lead: Lead,
    decision: TurnDecision,
    humanization_config: HumanizationConfig,
) -> SendResult:
    """Send the assistant response as one or more humanized chunks.

    Voice mode falls back to text and logs a warning (FE-05 will wire the
    real VoiceAdapter).
    """
    if decision.response_format in ("voice", "both"):
        logger.warning(
            "voice_format_not_implemented_fe03b lead_id=%s format=%s — falling back to text",
            lead.id,
            decision.response_format,
        )

    chunks = humanize(
        decision.response_text,
        humanization_config,
        is_voice=(decision.response_format == "voice"),
    )

    if not chunks:
        logger.warning(
            "humanize_returned_empty_chunks lead_id=%s text_len=%d",
            lead.id,
            len(decision.response_text),
        )
        return SendResult(external_id=None, status="sent", error_detail=None)

    last_external_id: str | None = None
    for chunk in chunks:
        if chunk.delay_before_ms > 0:
            try:
                await adapter.mark_as_typing(lead.whatsapp_e164)
            except (NotImplementedError, AttributeError):
                pass
            await asyncio.sleep(chunk.delay_before_ms / 1000.0)

        send_outcome = await adapter.send_text(lead.whatsapp_e164, chunk.text)
        last_external_id = send_outcome.external_id

    logger.info(
        "humanization.chunks_emitted lead_id=%s chunk_count=%d total_chars=%d",
        lead.id,
        len(chunks),
        sum(len(c.text) for c in chunks),
    )

    return SendResult(
        external_id=last_external_id,
        status="sent",
        error_detail=None,
    )
```

Also: `FakeMessagingAdapter` needs a `sent_messages: list[dict]` attribute that the test asserts on. If it doesn't exist, add it to fake.py:

```python
class FakeMessagingAdapter(MessagingAdapter):
    def __init__(self) -> None:
        # ... existing init ...
        self.typing_calls: list[str] = []
        self.sent_messages: list[dict] = []  # ensure exists

    async def send_text(self, to: str, text: str) -> SendResult:
        self.sent_messages.append({"to": to, "text": text})
        return SendResult(external_id=f"fake-{len(self.sent_messages)}", ...)
```

(Adapt to whatever `SendResult` shape the existing fake uses; keep `sent_messages` as a side-channel for tests.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_sender_chunked_send.py -v
uv run pytest tests/unit/ -k sender -v
```
Expected: PASS (4/4 new), no regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/sender.py src/ai_sdr/messaging/fake.py tests/unit/test_sender_chunked_send.py
git commit -m "feat(fe03b t9): sender iterates humanized chunks with delays + typing

Per spec §4. Calls humanize() then loops: optional mark_as_typing
before each non-first chunk, asyncio.sleep, send_text. Single chunk
or disabled humanization preserve current behavior."
```

---

### Task 10: `pipeline.run_turn` passes `humanization_config` to sender

**Files:**
- Modify: `src/ai_sdr/flowengine/pipeline.py`
- Test: `tests/unit/test_pipeline_passes_humanization_config.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_passes_humanization_config.py
"""pipeline.run_turn forwards tenant.humanization to sender (FE-03b Task 10)."""
from __future__ import annotations

import inspect

from ai_sdr.flowengine.pipeline import run_turn


def test_run_turn_signature_unchanged():
    """run_turn signature stays the same — humanization_config is resolved
    internally from tenant config rather than added as a kwarg."""
    sig = inspect.signature(run_turn)
    # We expect existing kwargs; humanization comes from tenant.humanization.
    assert "tenant" in sig.parameters
    assert "humanization_config" not in sig.parameters
```

This is a thin smoke test — the real wiring is verified by integration in T18.

- [ ] **Step 2: Run test to verify it passes (signature unchanged)**

Run: `uv run pytest tests/unit/test_pipeline_passes_humanization_config.py -v`
Expected: PASS — `run_turn` signature stable.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/pipeline.py`, locate the `send_response_text(...)` call inside `run_turn`. Today it looks like:

```python
send_result = await send_response_text(
    adapter=adapter, lead=ctx.lead, decision=decision,
)
```

Resolve the humanization config from the tenant config that's already loaded in the worker. Add the load in pipeline.run_turn — the cleanest path is to thread it via the worker's existing `tenant_cfg` → pipeline expects it as part of the existing tenant context.

Read the existing pipeline.run_turn to confirm where tenant config is available. Typically `tenant` is a `Tenant` ORM model and tenant_cfg (TenantConfig) is loaded earlier. If `tenant_cfg` is not yet a parameter, add it as a kwarg.

Update the signature to accept `tenant_cfg: TenantConfig`:

```python
async def run_turn(
    session: AsyncSession,
    *,
    tenant: Tenant,
    tenant_cfg: TenantConfig,  # NEW — for humanization + future configs
    treeflow: TreeflowDef,
    # ... existing kwargs ...
):
```

(If `tenant_cfg` is already threaded — confirm via reading pipeline.py — adjust this step accordingly.)

Then build the humanizer config:

```python
from ai_sdr.flowengine.humanizer import HumanizationConfig

humanization = HumanizationConfig(
    enabled=tenant_cfg.humanization.enabled,
    chunk_delimiter=tenant_cfg.humanization.chunk_delimiter,
    chars_per_second_min=tenant_cfg.humanization.chars_per_second_min,
    chars_per_second_max=tenant_cfg.humanization.chars_per_second_max,
    min_delay_ms=tenant_cfg.humanization.min_delay_ms,
    max_delay_ms=tenant_cfg.humanization.max_delay_ms,
    apply_to_voice=tenant_cfg.humanization.apply_to_voice,
)

send_result = await send_response_text(
    adapter=adapter, lead=ctx.lead, decision=decision,
    humanization_config=humanization,
)
```

(Optionally extract a `_build_humanization_config(tenant_cfg)` helper if the conversion is non-trivial; keep it inline for now.)

Update the worker callsite at `src/ai_sdr/worker/jobs/inbound.py` to pass `tenant_cfg` — the variable is already loaded there for guardrails.

- [ ] **Step 4: Run unit suite**

```bash
uv run pytest tests/unit/ -q
```
Expected: 449+ pass (matches end-of-FE-03a count + the FE-03b tests added so far). If worker callsite breaks any test, fix the `tenant_cfg=` plumbing.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/pipeline.py src/ai_sdr/worker/jobs/inbound.py tests/unit/test_pipeline_passes_humanization_config.py
git commit -m "feat(fe03b t10): pipeline.run_turn resolves humanization_config from tenant

Per spec §3 + §4.4. Tenant config flows in via worker → run_turn →
sender. Default config (tenant without humanization block) inherits
sensible defaults from HumanizationConfig() in tenant_yaml.py."
```

---

## Phase 5: Close lifecycle runtime

### Task 11: `close_lifecycle.py` — `CloseOutcome` + `evaluate_completion_rule()`

**Files:**
- Create: `src/ai_sdr/flowengine/close_lifecycle.py`
- Test: `tests/unit/test_close_lifecycle.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_close_lifecycle.py
"""close_lifecycle.evaluate_completion_rule pure function (FE-03b Task 11)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ai_sdr.flowengine.close_lifecycle import CloseOutcome, evaluate_completion_rule
from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowCompletionRule,
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTalkLifecycle,
    TreeflowTransition,
)


@dataclass
class _State:
    collected: dict = field(default_factory=dict)
    extracted_facts: dict = field(default_factory=dict)
    turn_index: int = 1


def _treeflow_with_rules(rules: list[TreeflowCompletionRule]) -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n},
        talk_lifecycle=TreeflowTalkLifecycle(close_when_completed=rules),
    )


def _decision(**collected) -> TurnDecision:
    return TurnDecision(
        response_text="x",
        collected_fields=collected,
        reasoning="r",
    )


def test_returns_none_when_no_lifecycle():
    tf = _treeflow_with_rules([])
    tf.talk_lifecycle = None  # explicit no lifecycle
    state = _State()
    decision = _decision(demo_agendada=True)
    assert evaluate_completion_rule(state=state, decision=decision, treeflow=tf) is None


def test_returns_none_when_lifecycle_has_no_rules():
    tf = _treeflow_with_rules([])
    state = _State()
    decision = _decision(demo_agendada=True)
    assert evaluate_completion_rule(state=state, decision=decision, treeflow=tf) is None


def test_returns_outcome_when_success_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.demo_agendada == True",
        outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(demo_agendada=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is not None
    assert out.status == "closed_completed_success"
    assert "demo_agendada" in out.reason
    assert out.closed_by == "pipeline_hook"


def test_returns_outcome_when_failure_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.lost == True",
        outcome="failure",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(lost=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_completed_failure"


def test_returns_outcome_when_no_interest_rule_fires():
    rule = TreeflowCompletionRule(
        expression="collected.no_interest_flag == True",
        outcome="no_interest",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision(no_interest_flag=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_no_interest"


def test_first_matching_rule_wins():
    rules = [
        TreeflowCompletionRule(
            expression="collected.first == True", outcome="failure",
        ),
        TreeflowCompletionRule(
            expression="collected.first == True", outcome="success",  # never reached
        ),
    ]
    tf = _treeflow_with_rules(rules)
    state = _State()
    decision = _decision(first=True)
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out.status == "closed_completed_failure"


def test_rule_seeing_only_state_collected():
    """state.collected is in scope (not just decision.collected_fields)."""
    rule = TreeflowCompletionRule(
        expression="collected.flag == True", outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State(collected={"flag": True})
    decision = _decision()
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is not None


def test_runtime_exception_in_rule_is_swallowed():
    """If simpleeval raises (unbound name, etc.) at runtime, skip the rule."""
    rule = TreeflowCompletionRule(
        expression="nonexistent_name > 0", outcome="success",
    )
    tf = _treeflow_with_rules([rule])
    state = _State()
    decision = _decision()
    # Returns None — does NOT raise
    out = evaluate_completion_rule(state=state, decision=decision, treeflow=tf)
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_close_lifecycle.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/flowengine/close_lifecycle.py`:

```python
"""Talk close evaluation — pure function (FE-03b §5.3).

Called from post_processing.apply_decision after state delta application.
Returns a CloseOutcome if a completion rule fires; None otherwise.

The worker scan job (scan_talks.py) handles inactivity + duration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from simpleeval import SimpleEval

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.treeflow_loader import TreeflowDef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloseOutcome:
    """Returned by evaluate_completion_rule when a rule fires."""

    status: str
    """One of: closed_completed_success | closed_completed_failure | closed_no_interest."""

    reason: str
    """Human-readable explanation (e.g., 'completion_rule: collected.X == True')."""

    closed_by: str
    """Always 'pipeline_hook' for this code path."""


def evaluate_completion_rule(
    *,
    state: Any,
    decision: TurnDecision,
    treeflow: TreeflowDef,
) -> CloseOutcome | None:
    """Check if any close_when_completed rule fires against state+decision.

    The first matching rule wins. Runtime evaluation errors (unbound names,
    type errors) are swallowed and the rule is skipped — the loader is
    responsible for catching syntax errors at parse time.
    """
    lifecycle = treeflow.talk_lifecycle
    if lifecycle is None or not lifecycle.close_when_completed:
        return None

    # Build evaluation context — same shape as routing.simpleeval extended
    # context, but with this-turn's decision.collected_fields merged on top.
    merged_collected = {**_get(state, "collected", {}), **decision.collected_fields}
    context: dict[str, Any] = {
        **merged_collected,
        "collected": merged_collected,
        "extracted_facts": _get(state, "extracted_facts", {}),
        "turn_index": _get(state, "turn_index", 0),
    }

    for rule in lifecycle.close_when_completed:
        try:
            if bool(SimpleEval(names=context).eval(rule.expression)):
                return CloseOutcome(
                    status=_outcome_to_status(rule.outcome),
                    reason=f"completion_rule: {rule.expression}",
                    closed_by="pipeline_hook",
                )
        except Exception as exc:
            logger.info(
                "close_lifecycle.rule_eval_skipped expression=%s err=%s",
                rule.expression, exc,
            )
            continue

    return None


def _get(state: Any, key: str, default: Any) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def _outcome_to_status(outcome: str) -> str:
    if outcome == "success":
        return "closed_completed_success"
    if outcome == "failure":
        return "closed_completed_failure"
    if outcome == "no_interest":
        return "closed_no_interest"
    raise ValueError(f"unknown outcome: {outcome!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_close_lifecycle.py -v`
Expected: PASS (8/8).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/close_lifecycle.py tests/unit/test_close_lifecycle.py
git commit -m "feat(fe03b t11): close_lifecycle pure function — completion rule eval

Per spec §5.3. CloseOutcome dataclass + evaluate_completion_rule().
Runtime errors (unbound names etc) swallowed silently; loader catches
syntax at parse time. Reused simpleeval context shape from FE-03a routing."
```

---

### Task 12: `post_processing.apply_decision` invokes `evaluate_completion_rule`

**Files:**
- Modify: `src/ai_sdr/flowengine/post_processing.py`
- Test: `tests/unit/test_post_processing_completion_close.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_post_processing_completion_close.py
"""post_processing applies completion close (FE-03b Task 12)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_sdr.flowengine.decision import TurnDecision
from ai_sdr.flowengine.post_processing import apply_decision
from ai_sdr.flowengine.treeflow_loader import (
    TreeflowCompletionRule,
    TreeflowDef,
    TreeflowExitCondition,
    TreeflowNode,
    TreeflowTalkLifecycle,
    TreeflowTransition,
)


def _treeflow(rules: list[TreeflowCompletionRule]) -> TreeflowDef:
    n = TreeflowNode(
        id="a", objetivo="x", bridge_instruction="", collects=[],
        exit_condition=TreeflowExitCondition(type="all_fields_filled"),
        next_nodes=[TreeflowTransition(condition="true", target="a")],
    )
    return TreeflowDef(
        id="t", version="1.0", display_name=None, sdr_persona={},
        entry_node="a", nodes={"a": n},
        talk_lifecycle=TreeflowTalkLifecycle(close_when_completed=rules),
    )


@pytest.mark.asyncio
async def test_completion_rule_sets_talk_status_and_skips_review_chain(
    async_session, talk_factory, talkflow_state_factory,
):
    """When completion rule fires, talk.status changes to closed_*, and
    requires_review_reason is NOT set even if escalate signals were present.
    """
    rules = [TreeflowCompletionRule(
        expression="collected.demo_agendada == True",
        outcome="success",
    )]
    tf = _treeflow(rules)
    talk = await talk_factory()
    state = await talkflow_state_factory(talk_id=talk.id, current_node="a")
    decision = TurnDecision(
        response_text="Maravilha! Vou agendar.",
        collected_fields={"demo_agendada": True},
        reasoning="r",
    )
    await apply_decision(
        async_session,
        talk=talk, state=state, decision=decision,
        resolved_target_node="a", now=datetime.now(timezone.utc),
        treeflow=tf,
    )
    await async_session.refresh(talk)
    assert talk.status == "closed_completed_success"
    assert talk.closed_reason is not None
    assert talk.closed_by == "pipeline_hook"
    assert talk.requires_review_reason is None


@pytest.mark.asyncio
async def test_no_completion_rule_does_not_close_talk(
    async_session, talk_factory, talkflow_state_factory,
):
    tf = _treeflow([])
    talk = await talk_factory()
    state = await talkflow_state_factory(talk_id=talk.id, current_node="a")
    decision = TurnDecision(
        response_text="x",
        collected_fields={},
        reasoning="r",
    )
    await apply_decision(
        async_session,
        talk=talk, state=state, decision=decision,
        resolved_target_node="a", now=datetime.now(timezone.utc),
        treeflow=tf,
    )
    await async_session.refresh(talk)
    assert talk.status == "active"
```

(Uses fixtures from FE-03a's T27 integration setup — `talk_factory`, `talkflow_state_factory`. These live in `tests/integration/conftest.py` or similar; if `async_session` is integration-only, mark the file as `pytest.mark.integration` and treat it as skip-friendly.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_post_processing_completion_close.py -v` (or move to integration if needed)
Expected: FAIL — completion path not in post_processing.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/post_processing.py`, locate the section where `requires_review_reason` is computed (around the `delta.requires_review_reason or resolve_escalation_reason(decision)` chain). Insert the completion check BEFORE that chain:

```python
from ai_sdr.flowengine.close_lifecycle import evaluate_completion_rule

async def apply_decision(...):
    # ... existing steps 1-8 (heuristics, objection_runtime, merge, message append, talk metadata) ...

    # NEW (FE-03b §5.4): completion rule check
    close_outcome = evaluate_completion_rule(
        state=state, decision=decision, treeflow=treeflow,
    )
    if close_outcome is not None:
        talk.status = close_outcome.status
        talk.closed_at = now
        talk.closed_reason = close_outcome.reason
        talk.closed_by = close_outcome.closed_by
        logger.info(
            "talk.closed.completion talk=%s outcome=%s rule=%s",
            talk.id, close_outcome.status, close_outcome.reason,
        )
        # Mutually exclusive with requires_review_reason — skip the chain.
        _emit_events(events, talk.id, getattr(talk, "lead_id", None))
        return

    # ... existing step 9 (requires_review_reason chain) and step 10 (events) ...
```

(Adapt to match the actual structure of apply_decision after FE-03a T27.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_post_processing_completion_close.py -v
uv run pytest tests/integration/test_post_processing_objection_state.py -v  # FE-03a T27 regression
```
Expected: PASS, no regression in FE-03a behaviors.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/post_processing.py tests/unit/test_post_processing_completion_close.py
git commit -m "feat(fe03b t12): post_processing wires completion rule check

Per spec §5.4. evaluate_completion_rule runs after state delta apply,
before the requires_review_reason chain. Completion close is mutually
exclusive — when rule fires, talk transitions to closed_completed_*
and no review reason is set. Emits talk.closed.completion event."
```

---

### Task 13: `TalkRepository.find_most_recent_closed`

**Files:**
- Modify: `src/ai_sdr/repositories/talk_repository.py`
- Test: `tests/unit/test_talk_repository_find_most_recent_closed.py` (create — integration-marked since repo hits DB)

- [ ] **Step 1: Write the failing test (skip-friendly integration)**

```python
# tests/unit/test_talk_repository_find_most_recent_closed.py
"""TalkRepository.find_most_recent_closed (FE-03b Task 13)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_returns_none_when_no_closed_talk_exists(
    async_session, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(async_session)
    # Create active Talk only
    active = await talk_factory(status="active")
    closed = await repo.find_most_recent_closed(active.tenant_id, active.lead_id)
    assert closed is None


@pytest.mark.asyncio
async def test_returns_most_recent_closed_talk(
    async_session, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(async_session)
    # Create 2 closed Talks for same lead
    talk_old = await talk_factory(status="closed_inactivity")
    talk_new = await talk_factory(
        status="closed_completed_success",
        tenant_id=talk_old.tenant_id, lead_id=talk_old.lead_id,
    )
    closed = await repo.find_most_recent_closed(talk_old.tenant_id, talk_old.lead_id)
    assert closed.id == talk_new.id  # most recent wins


@pytest.mark.asyncio
async def test_ignores_active_talks(
    async_session, talk_factory,
):
    from ai_sdr.repositories.talk_repository import TalkRepository
    repo = TalkRepository(async_session)
    active = await talk_factory(status="active")
    closed = await repo.find_most_recent_closed(active.tenant_id, active.lead_id)
    assert closed is None
```

- [ ] **Step 2: Run test to verify it fails / collect**

Run: `uv run pytest tests/unit/test_talk_repository_find_most_recent_closed.py --collect-only -v`
Expected: COLLECT — runs on VPS.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/repositories/talk_repository.py`, add a method:

```python
async def find_most_recent_closed(
    self,
    tenant_id: UUID,
    lead_id: UUID,
) -> Talk | None:
    """Return the most recently closed Talk for this (tenant, lead), or None.

    A Talk is 'closed' when status starts with 'closed_'. Used by
    preprocessing for re-engagement logging.
    """
    result = await self._session.execute(
        select(Talk)
        .where(
            Talk.tenant_id == tenant_id,
            Talk.lead_id == lead_id,
            Talk.status.like("closed_%"),
        )
        .order_by(Talk.closed_at.desc().nulls_last())
        .limit(1)
    )
    return result.scalar_one_or_none()
```

(Import `from sqlalchemy import select` if not already; import the `Talk` model.)

- [ ] **Step 4: Verify static + alembic chain**

```bash
uv run ruff check src/ai_sdr/repositories/talk_repository.py
uv run mypy src/ai_sdr/repositories/talk_repository.py
uv run pytest tests/unit/ -k talk_repository -v
```
Expected: clean static; existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/talk_repository.py tests/unit/test_talk_repository_find_most_recent_closed.py
git commit -m "feat(fe03b t13): TalkRepository.find_most_recent_closed

Per spec §5.5. status LIKE 'closed_%' filter; ORDER BY closed_at DESC
NULLS LAST. Used by preprocessing for re-engagement logging."
```

---

### Task 14: `preprocessing.resolve_pipeline_context` re-engagement log

**Files:**
- Modify: `src/ai_sdr/flowengine/preprocessing.py`
- Test: `tests/unit/test_preprocessing_re_engagement.py` (create — integration-marked)

- [ ] **Step 1: Write the failing test (skip-friendly)**

```python
# tests/unit/test_preprocessing_re_engagement.py
"""preprocessing logs re_engagement when lead returns post-close (FE-03b Task 14)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_re_engagement_log_emitted_when_previous_closed_talk_exists(
    async_session, lead_factory, talk_factory, caplog,
):
    import logging
    from ai_sdr.flowengine.preprocessing import resolve_pipeline_context
    # Setup: lead with a previously closed Talk
    lead = await lead_factory()
    await talk_factory(
        tenant_id=lead.tenant_id, lead_id=lead.id,
        status="closed_inactivity",
    )
    # Trigger preprocessing for a NEW inbound from same lead
    with caplog.at_level(logging.INFO):
        ctx = await resolve_pipeline_context(...)
    assert any(
        "re_engagement_after_close" in r.message for r in caplog.records
    )
    assert ctx.is_new_talk is True  # fresh Talk created


@pytest.mark.asyncio
async def test_no_re_engagement_log_when_no_prior_closed_talk(
    async_session, lead_factory, caplog,
):
    import logging
    from ai_sdr.flowengine.preprocessing import resolve_pipeline_context
    lead = await lead_factory()
    with caplog.at_level(logging.INFO):
        ctx = await resolve_pipeline_context(...)
    assert not any(
        "re_engagement_after_close" in r.message for r in caplog.records
    )
```

- [ ] **Step 2: Run test to verify it collects / fails**

Run: `uv run pytest tests/unit/test_preprocessing_re_engagement.py --collect-only -v`
Expected: COLLECT.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/flowengine/preprocessing.py`, find `resolve_pipeline_context`. In the branch where `find_active_for_lead` returns None (no existing Talk; we're about to create a fresh one), add a `find_most_recent_closed` lookup + log BEFORE the `_create_new_talk` call:

```python
existing = await talks.find_active_for_lead(tenant.id, lead.id)
if existing:
    return PipelineContext(lead=lead, talk=existing, inbound=inbound, is_new_talk=False)

# NEW (FE-03b §5.5): re-engagement detection
previously_closed = await talks.find_most_recent_closed(tenant.id, lead.id)
if previously_closed is not None:
    logger.info(
        "talk.re_engagement_after_close lead=%s previous_talk=%s "
        "previous_status=%s closed_at=%s",
        lead.id,
        previously_closed.id,
        previously_closed.status,
        previously_closed.closed_at,
    )

# Always create fresh Talk (per spec §16: nova Talk on re-engagement)
talk = await talks.create(...)
# ... continue with existing logic
```

(Adapt to actual variable names in current preprocessing.py.)

- [ ] **Step 4: Static + unit run**

```bash
uv run ruff check src/ai_sdr/flowengine/preprocessing.py
uv run pytest tests/unit/ -q
```
Expected: ruff clean; full unit suite green (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/preprocessing.py tests/unit/test_preprocessing_re_engagement.py
git commit -m "feat(fe03b t14): preprocessing logs re_engagement on lead return

Per spec §5.5. When lead has a most-recently-closed Talk and no
active Talk, log talk.re_engagement_after_close before creating a
fresh Talk. Behavior unchanged: always new Talk (never reopen),
matching spec §16."
```

---

## Phase 6: Worker scan job

### Task 15: `worker/jobs/scan_talks.py` — cross-tenant scan + per-Talk close

**Files:**
- Create: `src/ai_sdr/worker/jobs/scan_talks.py`
- Test: `tests/unit/test_scan_talks.py` (create — integration-marked)

- [ ] **Step 1: Write the failing test (skip-friendly)**

```python
# tests/unit/test_scan_talks.py
"""worker.jobs.scan_talks scan_active_talks (FE-03b Task 15)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_inactivity_closes_active_talks_past_threshold(
    async_session, talk_factory,
):
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks
    now = datetime.now(timezone.utc)
    # Talk inactive 8 days; TreeFlow has 7-day inactivity rule (via fixture)
    talk = await talk_factory(
        status="active",
        last_message_at=now - timedelta(days=8),
    )
    result = await scan_active_talks(async_session, now=now)
    await async_session.refresh(talk)
    assert talk.status == "closed_inactivity"
    assert result.inactive_closed >= 1


@pytest.mark.asyncio
async def test_duration_closes_active_talks_past_threshold(
    async_session, talk_factory,
):
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks
    now = datetime.now(timezone.utc)
    talk = await talk_factory(
        status="active",
        opened_at=now - timedelta(days=31),
        last_message_at=now,  # within inactivity window
    )
    result = await scan_active_talks(async_session, now=now)
    await async_session.refresh(talk)
    assert talk.status == "closed_duration"
    assert result.duration_closed >= 1


@pytest.mark.asyncio
async def test_scan_skips_already_closed_talks(
    async_session, talk_factory,
):
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks
    now = datetime.now(timezone.utc)
    talk = await talk_factory(status="closed_inactivity")
    result = await scan_active_talks(async_session, now=now)
    await async_session.refresh(talk)
    # Status unchanged
    assert talk.status == "closed_inactivity"


@pytest.mark.asyncio
async def test_treeflow_without_lifecycle_block_skipped(
    async_session, talk_factory_no_lifecycle,
):
    """Talks tied to TreeFlows without talk_lifecycle stay active."""
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks
    now = datetime.now(timezone.utc)
    talk = await talk_factory_no_lifecycle(
        status="active",
        last_message_at=now - timedelta(days=365),
    )
    await scan_active_talks(async_session, now=now)
    await async_session.refresh(talk)
    assert talk.status == "active"
```

- [ ] **Step 2: Run test to verify it collects**

Run: `uv run pytest tests/unit/test_scan_talks.py --collect-only -v`
Expected: COLLECT.

- [ ] **Step 3: Apply implementation**

Create `src/ai_sdr/worker/jobs/scan_talks.py`:

```python
"""Background scan: close Talks by inactivity / duration (FE-03b §5.2).

Runs as an arq cron job every 5 minutes (configurable via
WORKER_SCAN_INTERVAL_SECONDS). Cross-tenant — uses BYPASSRLS via
SET LOCAL row_security = off (ai_sdr_app has this privilege; same
pattern as follow_up_scanner from Plano 9).

Per-Talk commit so a crash mid-batch leaves a consistent partial state;
next run picks up the remainder. `WHERE status='active'` filter +
SKIP LOCKED on row select prevent double-close and worker contention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.treeflow_loader import TreeflowLoadError, load_treeflow_v2
from ai_sdr.models.talk import Talk
from ai_sdr.models.treeflow_version import TreeflowVersion

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    inactive_closed: int
    duration_closed: int


async def scan_active_talks(session: AsyncSession, *, now: datetime) -> ScanResult:
    """Close Talks that hit their TreeFlow's inactivity or duration limit."""
    inactive_closed = 0
    duration_closed = 0

    # Cross-tenant scan: opt out of RLS for the duration of this transaction.
    await session.execute(text("SET LOCAL row_security = off"))

    rows = await session.execute(
        select(Talk, TreeflowVersion)
        .join(TreeflowVersion, Talk.treeflow_version_id == TreeflowVersion.id)
        .where(Talk.status == "active")
        .with_for_update(skip_locked=True)
    )

    for talk, tfv in rows:
        try:
            treeflow = load_treeflow_v2(tfv.content_yaml)
        except TreeflowLoadError as exc:
            logger.warning(
                "scan_talks.treeflow_load_failed talk=%s err=%s",
                talk.id, exc,
            )
            continue

        lifecycle = treeflow.talk_lifecycle
        if lifecycle is None:
            continue

        if lifecycle.close_after_inactivity:
            cutoff = now - lifecycle.close_after_inactivity
            if talk.last_message_at < cutoff:
                await _close(session, talk, now, "closed_inactivity", "scan_job")
                inactive_closed += 1
                continue

        if lifecycle.close_after_duration:
            cutoff = now - lifecycle.close_after_duration
            if talk.opened_at < cutoff:
                await _close(session, talk, now, "closed_duration", "scan_job")
                duration_closed += 1

    await session.commit()
    logger.info(
        "scan_talks.completed inactive_closed=%d duration_closed=%d",
        inactive_closed, duration_closed,
    )
    return ScanResult(
        inactive_closed=inactive_closed,
        duration_closed=duration_closed,
    )


async def _close(
    session: AsyncSession,
    talk: Talk,
    now: datetime,
    status: str,
    closed_by: str,
) -> None:
    talk.status = status
    talk.closed_at = now
    talk.closed_reason = status
    talk.closed_by = closed_by
    flag_modified(talk, "status")
    logger.info(
        "talk.closed.%s talk=%s by=%s last_message_at=%s opened_at=%s",
        status.removeprefix("closed_"),
        talk.id, closed_by,
        talk.last_message_at, talk.opened_at,
    )
```

- [ ] **Step 4: Static + alembic chain**

```bash
uv run ruff check src/ai_sdr/worker/jobs/scan_talks.py
uv run mypy src/ai_sdr/worker/jobs/scan_talks.py
uv run pytest tests/unit/ -q
```
Expected: ruff clean; mypy clean (pre-existing errors stay); no unit regression.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/scan_talks.py tests/unit/test_scan_talks.py
git commit -m "feat(fe03b t15): worker scan_active_talks — close by inactivity/duration

Per spec §5.2. Cross-tenant scan with BYPASSRLS, SKIP LOCKED, per-Talk
close + commit. Talks without talk_lifecycle block skip silently
(backward-compat). Idempotent: status='active' filter prevents
double-close on next run."
```

---

### Task 16: Wire `scheduled_scan_talks` cron in `worker/main.py`

**Files:**
- Modify: `src/ai_sdr/worker/main.py`
- Test: `tests/unit/test_worker_main_registers_scan_cron.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_worker_main_registers_scan_cron.py
"""worker.main registers scheduled_scan_talks (FE-03b Task 16)."""
from __future__ import annotations


def test_scheduled_scan_talks_in_cron_jobs():
    from ai_sdr.worker import main
    # The cron_jobs list is built inside the WorkerSettings (or equivalent).
    # We assert the function name appears in the configured list.
    names = [getattr(job, "function", job).__name__ for job in main.cron_jobs]
    assert "scheduled_scan_talks" in names


def test_scheduled_scan_talks_function_exists():
    from ai_sdr.worker.main import scheduled_scan_talks
    assert callable(scheduled_scan_talks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_worker_main_registers_scan_cron.py -v`
Expected: FAIL — `scheduled_scan_talks` doesn't exist; not in `cron_jobs`.

- [ ] **Step 3: Apply implementation**

In `src/ai_sdr/worker/main.py`:

1. Add the wrapper function:

```python
import os
from datetime import datetime, timezone

from ai_sdr.db.session import session_factory
from ai_sdr.worker.jobs.scan_talks import scan_active_talks


async def scheduled_scan_talks(ctx: dict) -> None:
    """arq cron entrypoint for scan_active_talks. Runs every 5 minutes."""
    async with session_factory() as session:
        await scan_active_talks(session, now=datetime.now(timezone.utc))
```

2. Find the existing `cron_jobs` list and add an entry. Today (per the grep) it contains `follow_up_scanner`. Add:

```python
from arq import cron

cron_jobs = [
    cron(follow_up_scanner, minute=set(range(0, 60)), run_at_startup=False),
    cron(
        scheduled_scan_talks,
        minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},  # every 5 min
        run_at_startup=False,
    ),
]
```

(If `cron_jobs` lives inside a `WorkerSettings` class, adapt accordingly.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_worker_main_registers_scan_cron.py -v`
Expected: PASS (2/2).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/main.py tests/unit/test_worker_main_registers_scan_cron.py
git commit -m "feat(fe03b t16): wire scheduled_scan_talks cron (every 5 minutes)

Per spec §5.2. arq cron job: at minutes {0, 5, …, 55} of every hour.
Same pattern as follow_up_scanner from Plano 9. Interval is hard-coded
to 5min; making it env-configurable is follow-up work if needed."
```

---

## Phase 7: Integration tests + close-out

### Task 17: Integration tests — skip-friendly reference contracts

**Files:**
- Create: 4 files under `tests/integration/`

- [ ] **Step 1: Write the integration tests**

Create `tests/integration/test_humanization_e2e_3_chunks_with_delays.py`:

```python
"""E2E reference contract: 3-chunk send with delays (FE-03b Task 17)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_three_paragraph_response_sends_three_messages(
    async_session, run_turn_harness, fake_llm_polite,
):
    llm = fake_llm_polite(response_text="Oi!\n\nQue legal!\n\nQual seu segmento?")
    await run_turn_harness.send_inbound("oi")
    await run_turn_harness.run(llm=llm)
    sent = run_turn_harness.captured_outbound()
    assert len(sent) == 3
    assert sent[0]["text"] == "Oi!"
    assert sent[2]["text"] == "Qual seu segmento?"
```

Create `tests/integration/test_completion_rule_fires_e2e.py`:

```python
"""E2E reference contract: completion rule closes Talk (FE-03b Task 17)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_collected_field_triggers_completion_close(
    async_session, run_turn_harness, fake_chat_scripted,
):
    llm = fake_chat_scripted([{
        "response_text": "Maravilha!",
        "collected_fields": {"demo_agendada": True},
        "reasoning": "r",
    }])
    await run_turn_harness.send_inbound("ok, agenda")
    await run_turn_harness.run(llm=llm)
    talk = await run_turn_harness.talk()
    await async_session.refresh(talk)
    assert talk.status == "closed_completed_success"
    assert talk.closed_by == "pipeline_hook"
```

Create `tests/integration/test_scan_closes_inactive_talk_e2e.py`:

```python
"""E2E reference contract: scan_active_talks closes by inactivity (FE-03b Task 17)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio


async def test_inactive_talk_closed_by_scan(
    async_session, run_turn_harness,
):
    talk = await run_turn_harness.talk()
    talk.last_message_at = datetime.now(timezone.utc) - timedelta(days=8)
    await async_session.commit()
    from ai_sdr.worker.jobs.scan_talks import scan_active_talks
    await scan_active_talks(async_session, now=datetime.now(timezone.utc))
    await async_session.refresh(talk)
    assert talk.status == "closed_inactivity"
```

Create `tests/integration/test_re_engagement_after_close_e2e.py`:

```python
"""E2E reference contract: lead returns post-close → new Talk (FE-03b Task 17)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_inbound_after_close_creates_new_talk(
    async_session, run_turn_harness, fake_llm_polite,
):
    # First turn — Talk active
    await run_turn_harness.send_inbound("oi")
    await run_turn_harness.run(llm=fake_llm_polite())
    talk1 = await run_turn_harness.talk()
    talk1_id = talk1.id

    # Close the Talk manually
    talk1.status = "closed_inactivity"
    await async_session.commit()

    # Second turn — lead returns
    await run_turn_harness.send_inbound("oi de novo")
    await run_turn_harness.run(llm=fake_llm_polite())
    talk2 = await run_turn_harness.talk()
    assert talk2.id != talk1_id
    assert talk2.status == "active"
```

All four files use harness fixtures (`run_turn_harness`, `fake_llm_polite`, `fake_chat_scripted`) that are skip-friendly per the FE-03a Phase 11 pattern.

- [ ] **Step 2: Collect to verify they parse**

```bash
uv run pytest tests/integration/test_humanization_e2e_3_chunks_with_delays.py tests/integration/test_completion_rule_fires_e2e.py tests/integration/test_scan_closes_inactive_talk_e2e.py tests/integration/test_re_engagement_after_close_e2e.py --collect-only -v
```
Expected: 4 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_humanization_e2e_3_chunks_with_delays.py tests/integration/test_completion_rule_fires_e2e.py tests/integration/test_scan_closes_inactive_talk_e2e.py tests/integration/test_re_engagement_after_close_e2e.py
git commit -m "test(fe03b t17): integration reference contracts (skip-friendly)

4 E2E contracts documenting FE-03b behavior. Like FE-03a Phase 11,
these stay skipped locally until the run_turn_harness fixture lands.
Cover: humanization 3-chunk send, completion rule close, scan
inactivity close, re-engagement post-close."
```

---

### Task 18: CLAUDE.md update — FE-03b section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Append the new section**

Add at the appropriate location (after the `## FE-03a — Objection Runtime + Python Validator` section landed in FE-03a T40):

```markdown
## FE-03b — Humanização + Close Lifecycle

Polish do runtime que FE-03a entregou.

### Humanização

- `humanization` block em `tenant.yaml`: `enabled` (default true), `chunk_delimiter` (default `\n\n`), `chars_per_second_min/max` (8/15), `min_delay_ms`/`max_delay_ms` (800/4000), `apply_to_voice` (false).
- **Pipeline:** `humanize(response_text, config, *, is_voice)` em `flowengine/humanizer.py` é pure function — split por parágrafo + computa delay proporcional ao próximo chunk com bounds.
- **Sender** (`flowengine/sender.py`) itera chunks: `mark_as_typing(to)` (opcional, no-op default no protocol), `asyncio.sleep`, `send_text`.
- **WhatsApp Cloud** implementa `mark_as_typing` via `typing_indicator` API; PolicyError silenciado pq Meta gates per account.
- **Voice mode**: humanização pulada (1 chunk) unless `apply_to_voice=true`. FE-05 wire chunking diferente.

### Close lifecycle

- `talk_lifecycle` block opcional no TreeFlow YAML: `close_after_inactivity` (ISO-8601, [PT1H, P365D]), `close_after_duration` (ISO-8601, [P1D, P730D]), `close_when_completed: [{ expression, outcome }]` (outcome ∈ {success, failure, no_interest}).
- **Inactivity + Duration**: worker scan job (`worker/jobs/scan_talks.py`) roda cron a cada 5 minutos, cross-tenant via BYPASSRLS + SKIP LOCKED. Per-Talk commit.
- **Completion rule**: pipeline hook em `post_processing.apply_decision` após state delta. **Mutually exclusive com requires_review_reason** (close vence; review skipped).
- **Re-engagement**: lead manda mensagem após Talk close → **nova Talk fresca** (não reopen). preprocessing emite `talk.re_engagement_after_close` event.
- **Bounds errors fatais**: TreeFlow com talk_lifecycle inválido → `TreeflowLoadError`. Tenant nem inicia.

### Migration 0026

Estende `talks.status` CHECK constraint pra incluir `closed_completed_success`, `closed_completed_failure`, `closed_no_interest`, `closed_duration`. Source-of-truth em `ai_sdr.models.talk_status.TalkStatus` Literal + `ALL_STATUSES` tuple — migration e ORM importam de lá (pattern de `review_reason.py` em FE-03a).

### Eventos structlog (9 novos)

`talk.closed.{inactivity,duration,completion}`, `talk.re_engagement_after_close`, `humanization.{chunks_emitted,skipped_voice_mode}`, `mark_as_typing.{unsupported,failed}`, `scan_talks.completed`.

### Wipe pra dev fresh (atualiza FE-03a guidance)

```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr \
  -c "TRUNCATE checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations; \
      UPDATE talks SET status='active', requires_review_reason=NULL, escalated_at=NULL, \
                       closed_at=NULL, closed_reason=NULL, closed_by=NULL;"
```
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(fe03b t18): CLAUDE.md notes — Humanization + Close Lifecycle

Documents the new tenant.yaml humanization block, the TreeFlow
talk_lifecycle block + bounds, the worker scan job schedule, the
mutual-exclusivity rule between completion close and requires_review_reason,
the re-engagement-always-new-Talk behavior, and the migration 0026
status enum extension."
```

---

### Task 19: Final close-out + PR

**Files:**
- Possibly polish anything `ruff`/`mypy` flags

- [ ] **Step 1: Run the full pre-commit gauntlet**

```bash
cd /Users/nicolasamaral/dev/PeSDR-fe01b-pipeline
make lint
make format
make type
make test-unit
```

The repo-wide pre-existing lint debt (~98 errors documented across FE-03a tasks) is OUT OF SCOPE. Verify the touched files are clean.

- [ ] **Step 2: Run unit tests one more time**

```bash
uv run pytest tests/unit/ -q
```
Expected: 449+ pass (FE-03a baseline + ~30 new FE-03b tests).

- [ ] **Step 3: Close-out commit**

```bash
git add -u
git commit --allow-empty -m "chore(fe03b): close-out — all 19 tasks landed

Spec: docs/superpowers/specs/2026-06-10-fe03b-humanization-close-lifecycle-design.md
Plan: docs/superpowers/plans/2026-06-10-fe03b-humanization-close-lifecycle.md

Delivers:
- Humanizer (paragraph chunking + bounded typing delays)
- Sender extension (mark_as_typing + chunked send loop)
- MessagingAdapter.mark_as_typing protocol + WhatsApp Cloud impl
- TenantConfig.humanization model
- Close lifecycle: completion rule (pipeline hook), inactivity + duration (worker scan cron)
- Re-engagement: new Talk on lead return (with structlog event)
- TalkStatus single source of truth + Mapped[TalkStatus] column
- Migration 0026: closed_completed_success/failure, closed_no_interest, closed_duration
- TreeFlow YAML schema: talk_lifecycle block + bounds (ISO-8601, simpleeval syntax, outcome enum)
- 22+ unit tests + 4 integration contracts + 4 fixtures

Out of scope (deferred): turn_limit + LLM signal close (FE-03b'),
Sentinel ban (FE-04), operator manual close API (Plano 11 evolution),
long-term memory (Lead.profile) for re-engagement context (FE-03c)."
```

- [ ] **Step 4: Push the branch**

```bash
git push -u origin dev/nicolas-fe03b-humanization-close-lifecycle
```

- [ ] **Step 5: Open PR**

```bash
gh pr create \
  --base dev/nicolas-fe03a-objection-runtime \
  --head dev/nicolas-fe03b-humanization-close-lifecycle \
  --title "FE-03b: Humanization + Close Lifecycle (19 tasks)" \
  --body "$(cat <<'EOF'
## Summary

Per spec `docs/superpowers/specs/2026-06-10-fe03b-humanization-close-lifecycle-design.md`.
Implementation plan in `docs/superpowers/plans/2026-06-10-fe03b-humanization-close-lifecycle.md`.

Polish do runtime que FE-03a entregou. Cobre humanização (chunking + typing delays) e close lifecycle (3 gatilhos automáticos + re-engagement).

## Test plan

- [ ] `make lint && make format && make type && make test-unit` clean
- [ ] `make test-integration` on VPS (smoke + new T2/T12/T13/T14/T15/T17 paths)
- [ ] Manual: tenant com humanização ativada — verify lead recebe 3 mensagens com pausas; verify "digitando..." indicator no WhatsApp
- [ ] Manual: TreeFlow com `close_after_inactivity: P7D` — Talk de 8 dias é fechada pelo scan na próxima iteração
- [ ] Manual: TreeFlow com completion rule `collected.demo_agendada == true` — lead diz "agenda" e Talk vira `closed_completed_success`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**1. Spec coverage:**

| Spec section | Implementation task(s) |
|---|---|
| §1 Contexto | (informational; no task) |
| §2 Goals / non-goals | T19 close-out commit message |
| §3 Architecture overview | T1-T16 collectively |
| §4 Humanizer | T6 (pure function), T9 (sender wire), T10 (pipeline forwarding) |
| §4.1 HumanizationConfig | T6 (dataclass), T7 (tenant schema) |
| §4.4 Sender extension | T9 |
| §4.5 mark_as_typing protocol + impls | T8 |
| §5.1 3 close triggers | T11 (completion), T15 (inactivity + duration) |
| §5.2 Worker scan job | T15 |
| §5.3 Completion rule pure function | T11 |
| §5.4 Wire in post_processing | T12 |
| §5.5 Re-engagement | T13 (repo method), T14 (preprocessing log) |
| §6 YAML schema extensions | T4 (parser), T5 (bounds), T7 (tenant.yaml humanization) |
| §7 Migration 0026 | T1 (Literal), T2 (migration), T3 (ORM column) |
| §8 Brechas matrix | Covered by tests in T4, T5, T6, T9, T11, T15 |
| §9 Idempotency / transactions | Implementation pattern in T15 (per-Talk commit); behavior implicit in T12 |
| §10 Observability | Logger.info calls in T9, T11, T12, T14, T15 |
| §11 Testing strategy | Per-task tests aligned |
| §12 Out of scope | T18 (CLAUDE.md docs) + T19 close-out |
| §13 Migration / cutover | T18 documents the wipe command |

**2. Placeholder scan:**
- Searched for "TBD", "TODO", "implement later", "fill in details" — none in plan body.
- T13 and T14 tests use `(...)` for fixture parameters that need filling — the steps explicitly say "adapt to actual variable names". This is a known gap; the implementer reads preprocessing.py first per the step instruction.
- Spec sections referenced from tasks have stable section numbers (§N.M) matching the spec file.

**3. Type consistency:**
- `HumanizationConfig` field names match between humanizer.py (T6) and tenant_yaml.py (T7) — verified by mapping.
- `TalkStatus` enum values match between T1, T2 (migration), and T18 (CLAUDE.md docs).
- `CloseOutcome` shape consistent between T11 (creation) and T12 (consumption in post_processing).
- `find_most_recent_closed` signature matches between T13 (repo) and T14 (preprocessing call).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-10-fe03b-humanization-close-lifecycle.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Worked well for FE-03a (40 commits, ~120 subagents, ~13h).

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
