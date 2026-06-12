# FE-03c — On-Collected Actions + Adapter Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o pipeline genérico de **on-collected actions** + **adapter framework MVP** que fecha o ciclo FE-03 do FlowEngine v2. Sub-fase final: sem adapters reais, só ABC + registry + factory + 1 fake (`logging`).

**Architecture:** 9 módulos novos em `flowengine/actions/` (ABC, registry, factory, templating, fake, dispatcher) + 1 worker job (`worker/jobs/execute_action.py`) + 1 ORM model + 1 repository + 1 migration. Extensões pontuais em `treeflow_loader.py`, `post_processing.py`, `worker/main.py`. Single source of truth pro enum `action_executions.status` (Literal + `get_args()`).

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 async · Alembic · arq · Jinja2 (já na transitiva via LangChain) · pytest · structlog. Sem novas dependências externas.

**Spec fonte:** `docs/superpowers/specs/2026-06-12-fe03c-actions-adapter-framework-design.md` (commit `82a8a00`).

**Branch:** `dev/nicolas-fe03c-actions-adapter-framework` (criar a partir do head de `dev/nicolas-fe03b-humanization-close-lifecycle`).

**Worktree:** `/Users/nicolasamaral/dev/PeSDR-fe01b-pipeline`.

---

## File structure

### Novos arquivos
- `src/ai_sdr/models/action_status.py` — `ActionStatus` Literal + `ALL_STATUSES` tuple
- `src/ai_sdr/models/action_execution.py` — ORM model + ForeignKeys
- `src/ai_sdr/repositories/action_execution_repository.py` — `insert_pending`, `mark_executing`, `mark_success`, `mark_failed`
- `src/ai_sdr/flowengine/actions/__init__.py` — side-effect imports dos adapters (registra no registry)
- `src/ai_sdr/flowengine/actions/base.py` — `ActionAdapter` ABC + `ActionResult` dataclass
- `src/ai_sdr/flowengine/actions/registry.py` — `ACTION_ADAPTERS` dict + `@register` decorator
- `src/ai_sdr/flowengine/actions/factory.py` — `build_action_adapter` + `UnknownAdapterError`
- `src/ai_sdr/flowengine/actions/templating.py` — `SandboxedEnvironment` + `render_params` + `build_template_context` + `TemplateRenderError`
- `src/ai_sdr/flowengine/actions/fake.py` — `LoggingActionAdapter`
- `src/ai_sdr/flowengine/actions/dispatcher.py` — `dispatch_actions` function
- `src/ai_sdr/worker/jobs/execute_action.py` — `execute_action` arq job
- `migrations/versions/0028_action_executions.py` — DDL + RLS + constraints

### Arquivos modificados
- `src/ai_sdr/flowengine/treeflow_loader.py` — `OnCollectedAction` dataclass + parse `node.on_collected` + load-time validation
- `src/ai_sdr/flowengine/post_processing.py` — invoca `dispatch_actions` após merge de campos (antes de close lifecycle check)
- `src/ai_sdr/worker/main.py` — registra `execute_action` em `WorkerSettings.functions`
- `CLAUDE.md` — seção "Actions (FE-03c)"

### Arquivos de teste (`tests/unit/` flat)
14 novos arquivos.

### Arquivos de teste (`tests/integration/` flat)
3 novos arquivos (skip-friendly per pattern FE-03a/b).

---

## Phase 1: Single source of truth

### Task 1: `ActionStatus` Literal + `ALL_STATUSES`

**Files:**
- Create: `src/ai_sdr/models/action_status.py`
- Test: `tests/unit/test_action_status_literal_source_of_truth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_status_literal_source_of_truth.py
"""ActionStatus Literal exports canonical enum values (FE-03c Task 1)."""
from __future__ import annotations

from ai_sdr.models.action_status import ALL_STATUSES, ActionStatus


def test_all_statuses_has_expected_length():
    assert len(ALL_STATUSES) == 4


def test_all_statuses_includes_pending():
    assert "pending" in ALL_STATUSES


def test_all_statuses_includes_executing():
    assert "executing" in ALL_STATUSES


def test_all_statuses_includes_success():
    assert "success" in ALL_STATUSES


def test_all_statuses_includes_failed():
    assert "failed" in ALL_STATUSES


def test_literal_matches_tuple():
    """ALL_STATUSES is the canonical tuple — derived from the Literal via get_args."""
    from typing import get_args

    assert ALL_STATUSES == get_args(ActionStatus)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_status_literal_source_of_truth.py -v
```

Expected: `ImportError: cannot import name 'ActionStatus' from 'ai_sdr.models.action_status'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ai_sdr/models/action_status.py
"""Canonical Literal for `action_executions.status` (FE-03c Task 1).

Single source of truth across migration 0028, the ORM column, the worker
job, the repository, and the dispatcher. Keep in sync — if you add a
value here, update migration 0028's upgrade() to extend the CHECK
constraint.

Pattern mirrors ai_sdr.models.talk_status (FE-03b) and
ai_sdr.models.talk_closed_by (FE-03b hotfix).
"""

from __future__ import annotations

from typing import Literal, get_args

ActionStatus = Literal[
    "pending",
    "executing",
    "success",
    "failed",
]

ALL_STATUSES: tuple[str, ...] = get_args(ActionStatus)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_status_literal_source_of_truth.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/action_status.py tests/unit/test_action_status_literal_source_of_truth.py
git commit -m "feat(fe03c t1): ActionStatus Literal + ALL_STATUSES single source"
```

---

## Phase 2: Database (migration + ORM)

### Task 2: Migration 0028 — `action_executions` table + RLS + constraints

**Files:**
- Create: `migrations/versions/0028_action_executions.py`
- Test: `tests/integration/test_migration_0028_action_executions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_migration_0028_action_executions.py
"""Migration 0028 creates action_executions with constraints + RLS (FE-03c Task 2)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from ai_sdr.models.action_status import ALL_STATUSES
from ai_sdr.settings import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_tenant_lead_tfv_talk(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create the parent rows action_executions FK on (tenants, talks)."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}", "n": "t"},
    )
    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1.0', 'x', 'yaml')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await session.execute(
        text(
            "INSERT INTO talks "
            "(id, tenant_id, lead_id, treeflow_id, treeflow_version_id, "
            " status, handling_mode, last_message_at) "
            "VALUES (:tid, :ten, :lid, 'tf', :tfv, 'active', 'ai', now())"
        ),
        {"tid": talk_id, "ten": tenant_id, "lid": lead_id, "tfv": tfv_id},
    )
    return tenant_id, talk_id, lead_id


@pytest.mark.asyncio
async def test_status_check_constraint_accepts_valid(
    db_session: AsyncSession,
) -> None:
    """INSERTing each documented status succeeds."""
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    for v in ALL_STATUSES:
        sp = await db_session.begin_nested()
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'f', 'h', 'a', 'h', '{}'::jsonb, :v)"
            ),
            {"ten": tenant_id, "tid": talk_id, "v": v},
        )
        await sp.rollback()


@pytest.mark.asyncio
async def test_status_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    sp = await db_session.begin_nested()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'f', 'h', 'a', 'h', '{}'::jsonb, 'bogus')"
            ),
            {"ten": tenant_id, "tid": talk_id},
        )
    if sp.is_active:
        await sp.rollback()


@pytest.mark.asyncio
async def test_uniqueness_on_talk_field_value_hash(
    db_session: AsyncSession,
) -> None:
    """Second INSERT with same (talk_id, field, value_hash) violates UNIQUE."""
    tenant_id, talk_id, _ = await _seed_tenant_lead_tfv_talk(db_session)
    sp = await db_session.begin_nested()
    await db_session.execute(
        text(
            "INSERT INTO action_executions "
            "(tenant_id, talk_id, node_id, field, value_hash, "
            " adapter_name, handler, params_resolved, status) "
            "VALUES (:ten, :tid, 'n', 'demo_data', 'abc123', "
            " 'logging', 'schedule_event', '{}'::jsonb, 'pending')"
        ),
        {"ten": tenant_id, "tid": talk_id},
    )
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO action_executions "
                "(tenant_id, talk_id, node_id, field, value_hash, "
                " adapter_name, handler, params_resolved, status) "
                "VALUES (:ten, :tid, 'n', 'demo_data', 'abc123', "
                " 'logging', 'schedule_event', '{}'::jsonb, 'pending')"
            ),
            {"ten": tenant_id, "tid": talk_id},
        )
    if sp.is_active:
        await sp.rollback()


@pytest.mark.asyncio
async def test_pg_constraints_registered(async_engine: AsyncEngine) -> None:
    """CHECK constraint + UNIQUE constraint exist in pg_constraint."""
    async with async_engine.connect() as conn:
        check_row = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_action_executions_status' AND contype = 'c'"
            )
        )
        assert check_row.scalar() == 1
        uniq_row = await conn.execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'uq_action_executions_dedup' AND contype = 'u'"
            )
        )
        assert uniq_row.scalar() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/integration/test_migration_0028_action_executions.py -v
```

Expected: 4 ERRORs (`action_executions` table does not exist).

- [ ] **Step 3: Write the migration**

```python
# migrations/versions/0028_action_executions.py
"""action_executions table + RLS + constraints (FlowEngine FE-03c)

Per spec §5. Creates the table for tracking on_collected action lifecycle:
pending → executing → success | failed. UNIQUE (talk_id, field, value_hash)
enforces idempotency. RLS by tenant_id mirrors talks.

Revision ID: 0028_action_executions
Revises: 0027_talks_closed_by_lifecycle_values
Create Date: 2026-06-12 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from ai_sdr.models.action_status import ALL_STATUSES

revision = "0028_action_executions"
down_revision = "0027_talks_closed_by_lifecycle_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "action_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("value_hash", sa.Text(), nullable=False),
        sa.Column("adapter_name", sa.Text(), nullable=False),
        sa.Column("handler", sa.Text(), nullable=False),
        sa.Column("params_resolved", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
            name="ck_action_executions_status",
        ),
        sa.UniqueConstraint(
            "talk_id", "field", "value_hash", name="uq_action_executions_dedup"
        ),
    )
    op.create_index(
        "ix_action_executions_pending",
        "action_executions",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'executing')"),
    )
    op.create_index(
        "ix_action_executions_tenant_talk",
        "action_executions",
        ["tenant_id", "talk_id"],
    )

    # RLS — mirror the talks pattern.
    op.execute("ALTER TABLE action_executions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE action_executions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON action_executions "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON action_executions")
    op.drop_index("ix_action_executions_tenant_talk", table_name="action_executions")
    op.drop_index("ix_action_executions_pending", table_name="action_executions")
    op.drop_table("action_executions")
```

- [ ] **Step 4: Apply migration + run tests**

```bash
uv run alembic upgrade head
uv run pytest tests/integration/test_migration_0028_action_executions.py -v
```

Expected: alembic upgrade OK (creates `action_executions`), 4/4 PASS.

Note: this test only passes on the VPS (or local Docker if present). When developing locally without DB, skip with `-k "not integration"` and run on VPS before merge.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0028_action_executions.py tests/integration/test_migration_0028_action_executions.py
git commit -m "feat(fe03c t2): migration 0028 — action_executions table + RLS + constraints"
```

---

### Task 3: `ActionExecution` ORM model

**Files:**
- Create: `src/ai_sdr/models/action_execution.py`
- Test: `tests/unit/test_action_execution_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_execution_model.py
"""ActionExecution model exposes the expected columns (FE-03c Task 3).

Asserts on the SQLAlchemy table metadata rather than instantiating the model
— ORM instrumentation rejects __new__()-bypass in SQLAlchemy 2.x and the
model has many required FKs that would be noise to fixture here.
"""

from __future__ import annotations

from ai_sdr.models.action_execution import ActionExecution


def test_table_name():
    assert ActionExecution.__tablename__ == "action_executions"


def test_id_column_is_primary_key():
    col = ActionExecution.__table__.c.id
    assert col.primary_key is True


def test_tenant_id_not_nullable():
    assert ActionExecution.__table__.c.tenant_id.nullable is False


def test_talk_id_not_nullable():
    assert ActionExecution.__table__.c.talk_id.nullable is False


def test_value_hash_not_nullable():
    assert ActionExecution.__table__.c.value_hash.nullable is False


def test_params_resolved_is_jsonb():
    col = ActionExecution.__table__.c.params_resolved
    assert "JSONB" in str(col.type).upper()


def test_attempts_default_is_zero():
    col = ActionExecution.__table__.c.attempts
    assert col.server_default is not None


def test_last_error_nullable():
    assert ActionExecution.__table__.c.last_error.nullable is True


def test_external_id_nullable():
    assert ActionExecution.__table__.c.external_id.nullable is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_execution_model.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the model**

```python
# src/ai_sdr/models/action_execution.py
"""ActionExecution — ORM for action_executions table (FE-03c §5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base
from ai_sdr.models.action_status import ALL_STATUSES, ActionStatus


class ActionExecution(Base):
    __tablename__ = "action_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    talk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("talks.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(Text(), nullable=False)
    field: Mapped[str] = mapped_column(Text(), nullable=False)
    value_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    adapter_name: Mapped[str] = mapped_column(Text(), nullable=False)
    handler: Mapped[str] = mapped_column(Text(), nullable=False)
    params_resolved: Mapped[dict] = mapped_column(JSONB(), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Text(), nullable=False)
    attempts: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default=text("0")
    )
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{v}'" for v in ALL_STATUSES) + ")",
            name="ck_action_executions_status",
        ),
        UniqueConstraint(
            "talk_id", "field", "value_hash", name="uq_action_executions_dedup"
        ),
        Index(
            "ix_action_executions_pending",
            "status",
            "created_at",
            postgresql_where=text("status IN ('pending', 'executing')"),
        ),
        Index("ix_action_executions_tenant_talk", "tenant_id", "talk_id"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_execution_model.py -v
```

Expected: 9/9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/action_execution.py tests/unit/test_action_execution_model.py
git commit -m "feat(fe03c t3): ActionExecution ORM model"
```

---

## Phase 3: YAML schema + Loader

### Task 4: `OnCollectedAction` dataclass + TreeFlowLoader parsing

**Files:**
- Modify: `src/ai_sdr/flowengine/treeflow_loader.py`
- Test: `tests/unit/test_treeflow_loader_on_collected.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_treeflow_loader_on_collected.py
"""TreeFlowLoader parses node.on_collected with validation (FE-03c Task 4)."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.treeflow_loader import (
    OnCollectedAction,
    TreeflowLoadError,
    load_treeflow_v2,
)


_BASE_YAML = """
schema_version: 2
treeflow_id: test
version: '1.0'
entry_node: greeting
nodes:
  - id: greeting
    collect:
      - field: nome
        required: true
    transitions:
      - target: agendamento_demo
        when: "collected.nome != ''"
  - id: agendamento_demo
    collect:
      - field: demo_data
        required: true
    on_collected:
      - field: demo_data
        adapter: logging
        handler: schedule_event
        params:
          title: "Demo {{ collected.nome }}"
          duration_minutes: 30
"""


def test_on_collected_parsed_as_dataclass_list():
    tf = load_treeflow_v2(_BASE_YAML)
    node = next(n for n in tf.nodes if n.id == "agendamento_demo")
    assert len(node.on_collected) == 1
    action = node.on_collected[0]
    assert isinstance(action, OnCollectedAction)
    assert action.field == "demo_data"
    assert action.adapter == "logging"
    assert action.handler == "schedule_event"
    assert action.params == {
        "title": "Demo {{ collected.nome }}",
        "duration_minutes": 30,
    }


def test_on_collected_empty_list_when_missing():
    yaml = _BASE_YAML.replace(
        "    on_collected:\n"
        "      - field: demo_data\n"
        "        adapter: logging\n"
        "        handler: schedule_event\n"
        "        params:\n"
        "          title: \"Demo {{ collected.nome }}\"\n"
        "          duration_minutes: 30\n",
        "",
    )
    tf = load_treeflow_v2(yaml)
    node = next(n for n in tf.nodes if n.id == "agendamento_demo")
    assert node.on_collected == []


def test_on_collected_field_must_be_in_collect():
    yaml = _BASE_YAML.replace("field: demo_data\n        adapter:", "field: ghost\n        adapter:")
    with pytest.raises(TreeflowLoadError, match="field 'ghost' not in node.collect"):
        load_treeflow_v2(yaml)


def test_on_collected_handler_required():
    yaml = _BASE_YAML.replace(
        "        handler: schedule_event\n", "        handler: ''\n"
    )
    with pytest.raises(TreeflowLoadError, match="handler"):
        load_treeflow_v2(yaml)


def test_on_collected_template_syntax_error_is_fatal():
    yaml = _BASE_YAML.replace(
        'title: "Demo {{ collected.nome }}"',
        'title: "Demo {{ unclosed"',
    )
    with pytest.raises(TreeflowLoadError, match="template"):
        load_treeflow_v2(yaml)


def test_on_collected_unknown_adapter_is_warning_not_error(caplog):
    yaml = _BASE_YAML.replace("adapter: logging", "adapter: never_registered")
    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        tf = load_treeflow_v2(yaml)
    node = next(n for n in tf.nodes if n.id == "agendamento_demo")
    assert node.on_collected[0].adapter == "never_registered"
    assert any("never_registered" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_treeflow_loader_on_collected.py -v
```

Expected: `ImportError: cannot import name 'OnCollectedAction'`.

- [ ] **Step 3: Modify treeflow_loader.py**

Append the dataclass and parsing logic. Reference the existing structure of `TreeflowTalkLifecycle` (added in FE-03b T4) as the pattern.

```python
# Append to src/ai_sdr/flowengine/treeflow_loader.py (alongside other dataclasses)

from dataclasses import dataclass, field
from typing import Any

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment


@dataclass(frozen=True)
class OnCollectedAction:
    """A side-effect fired when a node field is collected (FE-03c §4)."""

    field: str
    adapter: str
    handler: str
    params: dict[str, Any] = field(default_factory=dict)


# Reuse a module-level sandboxed environment for load-time template parsing.
_TEMPLATE_PARSE_ENV = SandboxedEnvironment()


def _parse_on_collected(
    node_id: str,
    collect_fields: set[str],
    raw_list: Any,
) -> list[OnCollectedAction]:
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        raise TreeflowLoadError(
            f"node {node_id!r}: on_collected must be a list, got {type(raw_list).__name__}"
        )
    result: list[OnCollectedAction] = []
    for idx, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}] must be a mapping"
            )
        field_name = raw.get("field")
        if not field_name or not isinstance(field_name, str):
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}].field is required"
            )
        if field_name not in collect_fields:
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}].field {field_name!r} "
                f"not in node.collect"
            )
        adapter = raw.get("adapter")
        if not adapter or not isinstance(adapter, str):
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}].adapter is required"
            )
        handler = raw.get("handler")
        if not handler or not isinstance(handler, str):
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}].handler is required"
            )
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise TreeflowLoadError(
                f"node {node_id!r}: on_collected[{idx}].params must be a mapping"
            )
        _validate_template_syntax(node_id, idx, params)

        # Warn (not fail) when adapter is unknown — may be registered at runtime.
        from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS
        if adapter not in ACTION_ADAPTERS:
            import logging
            logging.getLogger(__name__).warning(
                "treeflow.on_collected.unknown_adapter node=%s adapter=%s",
                node_id, adapter,
            )

        result.append(
            OnCollectedAction(
                field=field_name,
                adapter=adapter,
                handler=handler,
                params=params,
            )
        )
    return result


def _validate_template_syntax(node_id: str, idx: int, params: Any) -> None:
    """Walk `params` recursively, parse each string as Jinja2; raise on syntax error."""
    def walk(node: Any) -> None:
        if isinstance(node, str):
            try:
                _TEMPLATE_PARSE_ENV.parse(node)
            except TemplateSyntaxError as exc:
                raise TreeflowLoadError(
                    f"node {node_id!r}: on_collected[{idx}].params template "
                    f"syntax error: {exc}"
                ) from exc
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(params)
```

In the existing `NodeSpec` dataclass, add the field:

```python
# Inside @dataclass NodeSpec definition (existing in treeflow_loader.py)
on_collected: list[OnCollectedAction] = field(default_factory=list)
```

In the existing node parsing block where `NodeSpec(...)` is constructed, add:

```python
# Where the loader builds NodeSpec(...) from raw dict:
collect_fields = {c["field"] for c in (raw.get("collect") or []) if isinstance(c, dict) and c.get("field")}
on_collected_actions = _parse_on_collected(
    node_id=raw["id"],
    collect_fields=collect_fields,
    raw_list=raw.get("on_collected"),
)
# pass to NodeSpec(...)
NodeSpec(
    ...,
    on_collected=on_collected_actions,
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_treeflow_loader_on_collected.py -v
uv run pytest tests/unit/test_treeflow_loader_v2.py -v  # existing test, must still pass
```

Expected: 6/6 new PASS + existing 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/treeflow_loader.py tests/unit/test_treeflow_loader_on_collected.py
git commit -m "feat(fe03c t4): OnCollectedAction dataclass + treeflow_loader parsing"
```

---

## Phase 4: Framework primitives

### Task 5: `ActionAdapter` ABC + `ActionResult`

**Files:**
- Create: `src/ai_sdr/flowengine/actions/__init__.py`
- Create: `src/ai_sdr/flowengine/actions/base.py`
- Test: `tests/unit/test_action_adapter_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_adapter_base.py
"""ActionAdapter ABC + ActionResult dataclass (FE-03c Task 5)."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult


def test_action_result_with_external_id():
    r = ActionResult(external_id="evt_123")
    assert r.external_id == "evt_123"
    assert r.detail is None


def test_action_result_with_detail():
    r = ActionResult(external_id="evt_123", detail={"echo": "ok"})
    assert r.detail == {"echo": "ok"}


def test_action_result_external_id_can_be_none():
    r = ActionResult(external_id=None)
    assert r.external_id is None


def test_cannot_instantiate_abstract_adapter():
    with pytest.raises(TypeError):
        ActionAdapter(tenant_config=None, secrets={})  # type: ignore[abstract]


def test_concrete_subclass_requires_name():
    """Subclasses without `name` attribute can be defined but registering fails (covered in T6)."""

    class Concrete(ActionAdapter):
        name = "concrete_test"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    inst = Concrete(tenant_config=None, secrets={})  # type: ignore[arg-type]
    assert inst.name == "concrete_test"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_adapter_base.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the ABC**

```python
# src/ai_sdr/flowengine/actions/__init__.py
"""FlowEngine action framework (FE-03c).

Side-effect imports below register adapters into the registry. Adding a
new adapter: import it here AND decorate the class with @register.
"""
from __future__ import annotations

from ai_sdr.flowengine.actions import fake  # noqa: F401 — registers LoggingActionAdapter
```

```python
# src/ai_sdr/flowengine/actions/base.py
"""ActionAdapter contract (FE-03c §7.1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_sdr.schemas.tenant_yaml import TenantConfig


@dataclass
class ActionResult:
    """Returned by ActionAdapter.execute on success."""

    external_id: str | None
    detail: dict[str, Any] | None = None


class ActionAdapter(ABC):
    """Contract for FE-03c action adapters.

    Idempotency note: workers may retry an execute() call after partial
    crashes. Adapters MUST be safe to re-call — either idempotent natively,
    or by detecting prior execution via external system query.
    """

    name: str  # class attribute; used as registry key

    def __init__(self, tenant_config: "TenantConfig", secrets: dict[str, str]) -> None:
        self.tenant = tenant_config
        self.secrets = secrets

    @abstractmethod
    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        """Run the action. Raise on failure (worker handles retry)."""
        ...
```

Note: the `__init__.py` will fail until Task 9 creates `fake.py`. For now, comment out the import in `__init__.py` (or use a try/except guard) to keep Task 5 self-contained:

```python
# src/ai_sdr/flowengine/actions/__init__.py
"""FlowEngine action framework (FE-03c).

Side-effect imports below register adapters into the registry. Adding a
new adapter: import it here AND decorate the class with @register.
"""
from __future__ import annotations

# fake adapter is wired in Task 9. Keep this file as the import surface.
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_adapter_base.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/__init__.py src/ai_sdr/flowengine/actions/base.py tests/unit/test_action_adapter_base.py
git commit -m "feat(fe03c t5): ActionAdapter ABC + ActionResult"
```

---

### Task 6: Action registry (`@register` + `ACTION_ADAPTERS`)

**Files:**
- Create: `src/ai_sdr/flowengine/actions/registry.py`
- Test: `tests/unit/test_action_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_registry.py
"""Action registry: @register decorator + ACTION_ADAPTERS dict (FE-03c Task 6)."""

from __future__ import annotations

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS, register


@pytest.fixture(autouse=True)
def reset_registry():
    """Snapshot/restore ACTION_ADAPTERS to keep tests independent."""
    snapshot = dict(ACTION_ADAPTERS)
    yield
    ACTION_ADAPTERS.clear()
    ACTION_ADAPTERS.update(snapshot)


def test_register_adds_to_dict():
    @register
    class A(ActionAdapter):
        name = "test_adapter_a"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    assert ACTION_ADAPTERS["test_adapter_a"] is A


def test_register_returns_class_unchanged():
    class B(ActionAdapter):
        name = "test_adapter_b"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    decorated = register(B)
    assert decorated is B


def test_register_rejects_missing_name():
    class NoName(ActionAdapter):
        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    with pytest.raises(ValueError, match="missing `name`"):
        register(NoName)


def test_register_rejects_duplicate_name():
    @register
    class C(ActionAdapter):
        name = "test_adapter_dup"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    class CAgain(ActionAdapter):
        name = "test_adapter_dup"

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    with pytest.raises(ValueError, match="already registered"):
        register(CAgain)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_registry.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the registry**

```python
# src/ai_sdr/flowengine/actions/registry.py
"""ActionAdapter registry (FE-03c §7.2).

Plug-and-play registration via @register decorator. Adding a new adapter:
1. Subclass ActionAdapter, set `name`, implement `execute`.
2. Decorate the class with @register.
3. Import the module in flowengine/actions/__init__.py (side-effect).
"""

from __future__ import annotations

from ai_sdr.flowengine.actions.base import ActionAdapter

ACTION_ADAPTERS: dict[str, type[ActionAdapter]] = {}


def register(adapter_cls: type[ActionAdapter]) -> type[ActionAdapter]:
    """Decorator: register an ActionAdapter under its `name` attribute."""
    name = getattr(adapter_cls, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError(
            f"{adapter_cls.__name__} missing `name` class attribute"
        )
    if name in ACTION_ADAPTERS:
        raise ValueError(f"adapter {name!r} already registered")
    ACTION_ADAPTERS[name] = adapter_cls
    return adapter_cls
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_registry.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/registry.py tests/unit/test_action_registry.py
git commit -m "feat(fe03c t6): action registry + @register decorator"
```

---

### Task 7: Action factory (`build_action_adapter`)

**Files:**
- Create: `src/ai_sdr/flowengine/actions/factory.py`
- Test: `tests/unit/test_action_factory.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_factory.py
"""Action factory: build_action_adapter + UnknownAdapterError (FE-03c Task 7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.factory import (
    UnknownAdapterError,
    build_action_adapter,
)
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS


@pytest.fixture(autouse=True)
def reset_registry():
    snapshot = dict(ACTION_ADAPTERS)
    yield
    ACTION_ADAPTERS.clear()
    ACTION_ADAPTERS.update(snapshot)


def _stub_tenant(slug="example"):
    return SimpleNamespace(slug=slug)


def test_build_instantiates_registered_adapter():
    constructed_with = {}

    class FactoryTestAdapter(ActionAdapter):
        name = "factory_test"

        def __init__(self, *, tenant_config, secrets):
            super().__init__(tenant_config=tenant_config, secrets=secrets)
            constructed_with["tenant"] = tenant_config
            constructed_with["secrets"] = secrets

        async def execute(self, *, handler, params):
            return ActionResult(external_id="ok")

    ACTION_ADAPTERS["factory_test"] = FactoryTestAdapter

    fake_secrets = {"some_key": "some_value"}
    with patch(
        "ai_sdr.flowengine.actions.factory.SopsLoader"
    ) as MockSops:
        loader_instance = MagicMock()
        loader_instance.load.return_value = fake_secrets
        MockSops.return_value = loader_instance

        adapter = build_action_adapter("factory_test", _stub_tenant("acme"))

    assert isinstance(adapter, FactoryTestAdapter)
    assert constructed_with["tenant"].slug == "acme"
    assert constructed_with["secrets"] == fake_secrets


def test_unknown_adapter_raises():
    with pytest.raises(UnknownAdapterError, match="not registered"):
        build_action_adapter("ghost_adapter_xyz", _stub_tenant())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_factory.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the factory**

```python
# src/ai_sdr/flowengine/actions/factory.py
"""Action adapter factory (FE-03c §7.3)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings

if TYPE_CHECKING:
    from ai_sdr.flowengine.actions.base import ActionAdapter
    from ai_sdr.schemas.tenant_yaml import TenantConfig


class UnknownAdapterError(Exception):
    """Raised when build_action_adapter is called for an unregistered name."""


def build_action_adapter(name: str, tenant: "TenantConfig") -> "ActionAdapter":
    if name not in ACTION_ADAPTERS:
        raise UnknownAdapterError(f"adapter {name!r} not registered")
    cls = ACTION_ADAPTERS[name]
    secrets_loader = SopsLoader(Path(get_settings().tenants_dir))
    secrets = secrets_loader.load(tenant.slug)
    return cls(tenant_config=tenant, secrets=secrets)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_factory.py -v
```

Expected: 2/2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/factory.py tests/unit/test_action_factory.py
git commit -m "feat(fe03c t7): action factory + UnknownAdapterError"
```

---

### Task 8: Templating (`SandboxedEnvironment` + `render_params` + `build_template_context`)

**Files:**
- Create: `src/ai_sdr/flowengine/actions/templating.py`
- Test: `tests/unit/test_action_templating.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_templating.py
"""Action templating: Jinja2 sandbox + render_params + build_template_context (FE-03c Task 8)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_sdr.flowengine.actions.templating import (
    TemplateRenderError,
    build_template_context,
    render_params,
)


def test_render_simple_string():
    ctx = {"collected": {"nome": "Joana"}}
    out = render_params({"title": "Demo {{ collected.nome }}"}, ctx)
    assert out == {"title": "Demo Joana"}


def test_render_passthrough_scalars():
    ctx = {"collected": {}}
    out = render_params(
        {"duration_minutes": 30, "active": True, "ratio": 0.5, "nothing": None}, ctx
    )
    assert out == {"duration_minutes": 30, "active": True, "ratio": 0.5, "nothing": None}


def test_render_nested_dict():
    ctx = {"collected": {"nome": "Joana"}}
    out = render_params(
        {"notification": {"subject": "Olá {{ collected.nome }}"}}, ctx
    )
    assert out == {"notification": {"subject": "Olá Joana"}}


def test_render_nested_list():
    ctx = {"collected": {"a": "1", "b": "2"}}
    out = render_params({"items": ["x{{ collected.a }}", "y{{ collected.b }}"]}, ctx)
    assert out == {"items": ["x1", "y2"]}


def test_undefined_var_raises_template_render_error():
    ctx = {"collected": {}}
    with pytest.raises(TemplateRenderError):
        render_params({"title": "Hi {{ collected.missing }}"}, ctx)


def test_sandbox_blocks_dunder_access():
    """SandboxedEnvironment blocks attribute access to dunders."""
    ctx = {"x": "abc"}
    with pytest.raises(TemplateRenderError):
        render_params({"oops": "{{ x.__class__.__mro__ }}"}, ctx)


def test_build_template_context_exposes_whitelisted_keys():
    state = SimpleNamespace(
        collected={"nome": "Joana"},
        extracted_facts={"timezone": "BR"},
    )
    decision = SimpleNamespace(collected_fields={"demo_data": "2026-06-13"})
    lead = SimpleNamespace(
        id="lead-1",
        whatsapp_e164="+5511999",
        external_label="Joana",
    )
    talk = SimpleNamespace(
        id="talk-1",
        treeflow_id="tf",
        turn_count=5,
    )
    ctx = build_template_context(state, decision, lead, talk)
    assert ctx["collected"] == {"nome": "Joana", "demo_data": "2026-06-13"}
    assert ctx["extracted_facts"] == {"timezone": "BR"}
    assert ctx["lead"]["whatsapp_e164"] == "+5511999"
    assert ctx["talk"]["turn_count"] == 5


def test_build_template_context_does_not_leak_tenant_id():
    state = SimpleNamespace(collected={}, extracted_facts={})
    decision = SimpleNamespace(collected_fields={})
    lead = SimpleNamespace(
        id="l", whatsapp_e164="+1", external_label="x",
        tenant_id="should-not-leak",
    )
    talk = SimpleNamespace(id="t", treeflow_id="tf", turn_count=0)
    ctx = build_template_context(state, decision, lead, talk)
    assert "tenant_id" not in ctx["lead"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_templating.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the templating module**

```python
# src/ai_sdr/flowengine/actions/templating.py
"""Action parameter templating (FE-03c §8).

Jinja2 sandboxed environment. Renders dict/list/str recursively, scalars
passthrough. Undefined variables and sandbox violations raise
TemplateRenderError, which the dispatcher catches and logs as
`action.dispatch.template_render_failed`.
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment


class TemplateRenderError(Exception):
    """Wraps any Jinja2 render-time failure (undefined, sandbox, etc)."""


_ENV = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
)


def render_params(template: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Render strings recursively; dicts/lists traversed; scalars passthrough."""
    def walk(node: Any) -> Any:
        if isinstance(node, str):
            try:
                return _ENV.from_string(node).render(**context)
            except TemplateError as exc:
                raise TemplateRenderError(str(exc)) from exc
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node
    rendered = walk(template)
    if not isinstance(rendered, dict):
        raise TemplateRenderError(f"top-level template must be a dict, got {type(rendered).__name__}")
    return rendered


def build_template_context(state: Any, decision: Any, lead: Any, talk: Any) -> dict[str, Any]:
    """Build the whitelisted context dict exposed to Jinja2.

    Whitelist scope: only fields that adapters are expected to need.
    Notably excludes lead.tenant_id (security) and full ORM objects
    (avoid lazy-load surprises in the sandbox).
    """
    merged_collected = {**state.collected, **decision.collected_fields}
    return {
        "collected": merged_collected,
        "extracted_facts": state.extracted_facts,
        "lead": {
            "id": str(lead.id),
            "whatsapp_e164": lead.whatsapp_e164,
            "external_label": lead.external_label,
        },
        "talk": {
            "id": str(talk.id),
            "treeflow_id": talk.treeflow_id,
            "turn_count": talk.turn_count,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_templating.py -v
```

Expected: 8/8 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/templating.py tests/unit/test_action_templating.py
git commit -m "feat(fe03c t8): action templating — Jinja2 sandbox + render_params + context"
```

---

### Task 9: `LoggingActionAdapter` (fake adapter)

**Files:**
- Create: `src/ai_sdr/flowengine/actions/fake.py`
- Modify: `src/ai_sdr/flowengine/actions/__init__.py`
- Test: `tests/unit/test_logging_action_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_logging_action_adapter.py
"""LoggingActionAdapter (fake) (FE-03c Task 9)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from ai_sdr.flowengine.actions.fake import LoggingActionAdapter
from ai_sdr.flowengine.actions.registry import ACTION_ADAPTERS


def test_registered_under_logging_name():
    assert ACTION_ADAPTERS.get("logging") is LoggingActionAdapter


@pytest.mark.asyncio
async def test_execute_returns_deterministic_fake_id():
    tenant = SimpleNamespace(slug="t1")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    r1 = await adapter.execute(handler="schedule_event", params={"a": 1})
    r2 = await adapter.execute(handler="schedule_event", params={"a": 1})
    assert r1.external_id == r2.external_id
    assert r1.external_id.startswith("fake-schedule_event-")


@pytest.mark.asyncio
async def test_execute_includes_params_in_detail():
    tenant = SimpleNamespace(slug="t1")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    r = await adapter.execute(handler="x", params={"a": 1, "b": 2})
    assert r.detail == {"echo": {"a": 1, "b": 2}}


@pytest.mark.asyncio
async def test_execute_logs(caplog):
    tenant = SimpleNamespace(slug="acme_test_slug")
    adapter = LoggingActionAdapter(tenant_config=tenant, secrets={})
    with caplog.at_level(logging.INFO):
        await adapter.execute(handler="schedule_event", params={"a": 1})
    assert any("acme_test_slug" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_logging_action_adapter.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the fake adapter**

```python
# src/ai_sdr/flowengine/actions/fake.py
"""LoggingActionAdapter — dev/test fake (FE-03c §7.4).

Determinístico para testes. Não toca nenhum sistema externo; loga e
retorna um external_id fake derivado de sha256(params).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ai_sdr.flowengine.actions.base import ActionAdapter, ActionResult
from ai_sdr.flowengine.actions.registry import register

logger = logging.getLogger(__name__)


@register
class LoggingActionAdapter(ActionAdapter):
    name = "logging"

    async def execute(self, *, handler: str, params: dict[str, Any]) -> ActionResult:
        logger.info(
            "logging_adapter.executed tenant=%s handler=%s params=%s",
            getattr(self.tenant, "slug", "?"),
            handler,
            params,
        )
        canonical = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:8]
        fake_id = f"fake-{handler}-{digest}"
        return ActionResult(external_id=fake_id, detail={"echo": params})
```

Update `__init__.py` to wire the import:

```python
# src/ai_sdr/flowengine/actions/__init__.py
"""FlowEngine action framework (FE-03c).

Side-effect imports below register adapters into the registry.
"""
from __future__ import annotations

from ai_sdr.flowengine.actions import fake  # noqa: F401 — registers LoggingActionAdapter
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_logging_action_adapter.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/fake.py src/ai_sdr/flowengine/actions/__init__.py tests/unit/test_logging_action_adapter.py
git commit -m "feat(fe03c t9): LoggingActionAdapter fake + registered"
```

---

## Phase 5: Dispatcher + Repository + Worker

### Task 10: `ActionExecutionRepository`

**Files:**
- Create: `src/ai_sdr/repositories/action_execution_repository.py`
- Test: `tests/unit/test_action_execution_repository_signatures.py`

- [ ] **Step 1: Write the failing test**

Repository DB ops are exercised end-to-end in integration tests (Task 14). The unit test here only verifies the public surface: method names, async-ness, parameter shape. This avoids needing a live session.

```python
# tests/unit/test_action_execution_repository_signatures.py
"""ActionExecutionRepository surface (FE-03c Task 10)."""

from __future__ import annotations

import inspect

from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository


def test_class_exists():
    assert ActionExecutionRepository is not None


def test_insert_pending_is_async():
    sig = inspect.signature(ActionExecutionRepository.insert_pending)
    assert inspect.iscoroutinefunction(ActionExecutionRepository.insert_pending)
    params = list(sig.parameters)
    # self + 8 kw-only fields
    assert params == [
        "self", "tenant_id", "talk_id", "node_id", "field",
        "value_hash", "adapter_name", "handler", "params_resolved",
    ]


def test_mark_executing_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_executing)


def test_mark_success_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_success)


def test_mark_failed_is_async():
    assert inspect.iscoroutinefunction(ActionExecutionRepository.mark_failed)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_execution_repository_signatures.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the repository**

```python
# src/ai_sdr/repositories/action_execution_repository.py
"""DB ops for action_executions (FE-03c §3.1).

insert_pending uses ON CONFLICT DO NOTHING to enforce idempotency:
re-emitting the same (talk_id, field, value_hash) tuple returns None and
the dispatcher skips enqueue (logs `action.dispatch.skipped_duplicate`).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.action_execution import ActionExecution


class ActionExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_pending(
        self,
        *,
        tenant_id: uuid.UUID,
        talk_id: uuid.UUID,
        node_id: str,
        field: str,
        value_hash: str,
        adapter_name: str,
        handler: str,
        params_resolved: dict[str, Any],
    ) -> uuid.UUID | None:
        """INSERT … ON CONFLICT DO NOTHING. Returns new id, or None if duplicate."""
        stmt = (
            pg_insert(ActionExecution)
            .values(
                tenant_id=tenant_id,
                talk_id=talk_id,
                node_id=node_id,
                field=field,
                value_hash=value_hash,
                adapter_name=adapter_name,
                handler=handler,
                params_resolved=params_resolved,
                status="pending",
            )
            .on_conflict_do_nothing(constraint="uq_action_executions_dedup")
            .returning(ActionExecution.id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return row.id if row is not None else None

    async def mark_executing(self, execution_id: uuid.UUID) -> ActionExecution | None:
        """SELECT FOR UPDATE + status='executing' + attempts+1. Returns row or None."""
        stmt = (
            select(ActionExecution)
            .where(ActionExecution.id == execution_id)
            .with_for_update()
        )
        result = await self._session.execute(stmt)
        execution = result.scalar_one_or_none()
        if execution is None:
            return None
        execution.status = "executing"
        execution.attempts = (execution.attempts or 0) + 1
        return execution

    async def mark_success(
        self, execution: ActionExecution, *, external_id: str | None
    ) -> None:
        execution.status = "success"
        execution.external_id = external_id

    async def mark_failed(
        self, execution: ActionExecution, *, error: str, terminal: bool
    ) -> None:
        execution.last_error = (error or "")[:1000]
        if terminal:
            execution.status = "failed"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_execution_repository_signatures.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/action_execution_repository.py tests/unit/test_action_execution_repository_signatures.py
git commit -m "feat(fe03c t10): ActionExecutionRepository"
```

---

### Task 11: `dispatch_actions` function

**Files:**
- Create: `src/ai_sdr/flowengine/actions/dispatcher.py`
- Test: `tests/unit/test_action_dispatcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_action_dispatcher.py
"""dispatch_actions: skips wrong-field actions, enqueues good ones, swallows render errors (FE-03c Task 11)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from ai_sdr.flowengine.actions.dispatcher import dispatch_actions
from ai_sdr.flowengine.treeflow_loader import OnCollectedAction


def _make_node(actions):
    return SimpleNamespace(id="agendamento_demo", on_collected=actions)


def _make_state(collected=None):
    return SimpleNamespace(
        collected=collected or {},
        extracted_facts={},
    )


def _make_decision(collected_fields=None):
    return SimpleNamespace(collected_fields=collected_fields or {})


def _make_talk():
    return SimpleNamespace(
        id=uuid4(), tenant_id=uuid4(), treeflow_id="tf", turn_count=1,
    )


def _make_lead():
    return SimpleNamespace(
        id=uuid4(), whatsapp_e164="+5511999", external_label="x",
    )


@pytest.mark.asyncio
async def test_dispatch_skipped_when_field_not_in_collected_fields():
    """LLM didn't emit demo_data this turn → action does NOT enqueue."""
    actions = [OnCollectedAction(
        field="demo_data", adapter="logging", handler="schedule_event",
        params={"title": "hi"},
    )]
    node = _make_node(actions)
    state = _make_state()
    decision = _make_decision(collected_fields={"nome": "joana"})  # NOT demo_data
    talk = _make_talk()
    lead = _make_lead()

    repo = MagicMock()
    repo.insert_pending = AsyncMock()
    enqueue = AsyncMock()

    await dispatch_actions(
        session=MagicMock(),
        repo=repo,
        enqueue=enqueue,
        state=state,
        decision=decision,
        node_spec=node,
        talk=talk,
        lead=lead,
    )
    repo.insert_pending.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_enqueues_when_field_collected():
    actions = [OnCollectedAction(
        field="demo_data", adapter="logging", handler="schedule_event",
        params={"title": "Demo {{ collected.nome }}"},
    )]
    node = _make_node(actions)
    state = _make_state(collected={"nome": "joana"})
    decision = _make_decision(collected_fields={"demo_data": "2026-06-13"})
    talk = _make_talk()
    lead = _make_lead()

    repo = MagicMock()
    new_id = uuid4()
    repo.insert_pending = AsyncMock(return_value=new_id)
    enqueue = AsyncMock()

    await dispatch_actions(
        session=MagicMock(),
        repo=repo,
        enqueue=enqueue,
        state=state,
        decision=decision,
        node_spec=node,
        talk=talk,
        lead=lead,
    )
    repo.insert_pending.assert_awaited_once()
    kwargs = repo.insert_pending.await_args.kwargs
    assert kwargs["field"] == "demo_data"
    assert kwargs["params_resolved"] == {"title": "Demo joana"}
    enqueue.assert_awaited_once_with(str(new_id))


@pytest.mark.asyncio
async def test_dispatch_skipped_duplicate_logs_and_skips_enqueue(caplog):
    actions = [OnCollectedAction(
        field="demo_data", adapter="logging", handler="schedule_event",
        params={"title": "x"},
    )]
    node = _make_node(actions)
    decision = _make_decision(collected_fields={"demo_data": "2026-06-13"})

    repo = MagicMock()
    repo.insert_pending = AsyncMock(return_value=None)  # UNIQUE collision
    enqueue = AsyncMock()

    with caplog.at_level(logging.INFO):
        await dispatch_actions(
            session=MagicMock(), repo=repo, enqueue=enqueue,
            state=_make_state(), decision=decision,
            node_spec=node, talk=_make_talk(), lead=_make_lead(),
        )
    enqueue.assert_not_awaited()
    assert any("skipped_duplicate" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_template_error_logs_and_skips(caplog):
    actions = [OnCollectedAction(
        field="demo_data", adapter="logging", handler="x",
        params={"title": "Hello {{ collected.missing }}"},  # undefined
    )]
    node = _make_node(actions)
    decision = _make_decision(collected_fields={"demo_data": "2026-06-13"})

    repo = MagicMock()
    repo.insert_pending = AsyncMock()
    enqueue = AsyncMock()

    with caplog.at_level(logging.WARNING):
        await dispatch_actions(
            session=MagicMock(), repo=repo, enqueue=enqueue,
            state=_make_state(), decision=decision,
            node_spec=node, talk=_make_talk(), lead=_make_lead(),
        )
    repo.insert_pending.assert_not_awaited()
    enqueue.assert_not_awaited()
    assert any("template_render_failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_empty_on_collected_short_circuits():
    node = _make_node([])
    repo = MagicMock()
    repo.insert_pending = AsyncMock()
    enqueue = AsyncMock()

    await dispatch_actions(
        session=MagicMock(), repo=repo, enqueue=enqueue,
        state=_make_state(),
        decision=_make_decision(collected_fields={"x": 1}),
        node_spec=node, talk=_make_talk(), lead=_make_lead(),
    )
    repo.insert_pending.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_action_dispatcher.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the dispatcher**

```python
# src/ai_sdr/flowengine/actions/dispatcher.py
"""dispatch_actions — entrypoint from post_processing (FE-03c §6.1)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.actions.templating import (
    TemplateRenderError,
    build_template_context,
    render_params,
)
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository

logger = logging.getLogger(__name__)


def _hash_value(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def dispatch_actions(
    *,
    session: AsyncSession,
    repo: ActionExecutionRepository,
    enqueue: Callable[[str], Awaitable[None]],
    state: Any,
    decision: Any,
    node_spec: Any,
    talk: Any,
    lead: Any,
) -> None:
    """For each `node.on_collected` whose field appears in TurnDecision,
    insert a pending action_executions row + enqueue the arq job.

    No-op if `node.on_collected` is empty. Template render failures and
    UNIQUE collisions log and skip (don't raise).
    """
    on_collected_list = list(getattr(node_spec, "on_collected", []) or [])
    if not on_collected_list:
        return

    collected_fields = getattr(decision, "collected_fields", {}) or {}
    context = build_template_context(state, decision, lead, talk)

    for action_spec in on_collected_list:
        if action_spec.field not in collected_fields:
            continue

        try:
            params_resolved = render_params(action_spec.params, context)
        except TemplateRenderError as exc:
            logger.warning(
                "action.dispatch.template_render_failed talk=%s field=%s err=%s",
                getattr(talk, "id", "?"),
                action_spec.field,
                exc,
            )
            continue

        value = collected_fields[action_spec.field]
        value_hash = _hash_value(value)

        execution_id = await repo.insert_pending(
            tenant_id=talk.tenant_id,
            talk_id=talk.id,
            node_id=node_spec.id,
            field=action_spec.field,
            value_hash=value_hash,
            adapter_name=action_spec.adapter,
            handler=action_spec.handler,
            params_resolved=params_resolved,
        )
        if execution_id is None:
            logger.info(
                "action.dispatch.skipped_duplicate talk=%s field=%s value_hash=%s",
                talk.id,
                action_spec.field,
                value_hash,
            )
            continue

        await enqueue(str(execution_id))
        logger.info(
            "action.enqueued execution=%s adapter=%s handler=%s",
            execution_id,
            action_spec.adapter,
            action_spec.handler,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_action_dispatcher.py -v
```

Expected: 5/5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/actions/dispatcher.py tests/unit/test_action_dispatcher.py
git commit -m "feat(fe03c t11): dispatch_actions function"
```

---

### Task 12: `execute_action` worker job

**Files:**
- Create: `src/ai_sdr/worker/jobs/execute_action.py`
- Test: `tests/unit/test_execute_action_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_execute_action_worker.py
"""execute_action worker job (FE-03c Task 12).

Test surface: success path, terminal failure (attempts >= 3), retry path
(attempts < 3 → raise so arq re-enqueues), execution-not-found early return.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from ai_sdr.flowengine.actions.base import ActionResult
from ai_sdr.worker.jobs.execute_action import execute_action


@asynccontextmanager
async def _fake_session_factory_cm(session_mock):
    yield session_mock


@pytest.fixture
def fake_ctx():
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()

    def factory():
        return _fake_session_factory_cm(session_mock)

    return {
        "session_factory": factory,
        "_session": session_mock,
    }


@pytest.mark.asyncio
async def test_execution_not_found_returns_early(fake_ctx):
    execution_id = uuid4()
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=None)

    with patch(
        "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
        return_value=repo_mock,
    ):
        await execute_action(fake_ctx, str(execution_id))

    repo_mock.mark_executing.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_path_marks_success_and_commits(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id, tenant_id=uuid4(),
        adapter_name="logging", handler="schedule_event",
        params_resolved={"a": 1}, attempts=0,
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_success = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(return_value=ActionResult(external_id="ext-1"))

    with patch(
        "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
        return_value=repo_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action.build_action_adapter",
        return_value=adapter_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
        AsyncMock(return_value=SimpleNamespace(slug="t1")),
    ), patch(
        "ai_sdr.worker.jobs.execute_action.set_tenant_context",
        AsyncMock(),
    ):
        await execute_action(fake_ctx, str(execution_id))

    adapter_mock.execute.assert_awaited_once_with(
        handler="schedule_event", params={"a": 1}
    )
    repo_mock.mark_success.assert_awaited_once()
    assert repo_mock.mark_success.await_args.kwargs["external_id"] == "ext-1"


@pytest.mark.asyncio
async def test_retry_path_raises_so_arq_reenqueues(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id, tenant_id=uuid4(),
        adapter_name="logging", handler="x",
        params_resolved={}, attempts=1,  # post-increment in mark_executing
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_failed = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
        return_value=repo_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action.build_action_adapter",
        return_value=adapter_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
        AsyncMock(return_value=SimpleNamespace(slug="t1")),
    ), patch(
        "ai_sdr.worker.jobs.execute_action.set_tenant_context",
        AsyncMock(),
    ), pytest.raises(RuntimeError, match="boom"):
        await execute_action(fake_ctx, str(execution_id))

    # mark_failed called with terminal=False
    repo_mock.mark_failed.assert_awaited_once()
    assert repo_mock.mark_failed.await_args.kwargs["terminal"] is False


@pytest.mark.asyncio
async def test_terminal_failure_after_3_attempts(fake_ctx):
    execution_id = uuid4()
    fake_execution = SimpleNamespace(
        id=execution_id, tenant_id=uuid4(),
        adapter_name="logging", handler="x",
        params_resolved={}, attempts=3,  # already at max
    )
    repo_mock = MagicMock()
    repo_mock.mark_executing = AsyncMock(return_value=fake_execution)
    repo_mock.mark_failed = AsyncMock()

    adapter_mock = MagicMock()
    adapter_mock.execute = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "ai_sdr.worker.jobs.execute_action.ActionExecutionRepository",
        return_value=repo_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action.build_action_adapter",
        return_value=adapter_mock,
    ), patch(
        "ai_sdr.worker.jobs.execute_action._load_tenant_by_id",
        AsyncMock(return_value=SimpleNamespace(slug="t1")),
    ), patch(
        "ai_sdr.worker.jobs.execute_action.set_tenant_context",
        AsyncMock(),
    ):
        # Does NOT raise — terminal failure returns
        await execute_action(fake_ctx, str(execution_id))

    repo_mock.mark_failed.assert_awaited_once()
    assert repo_mock.mark_failed.await_args.kwargs["terminal"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_execute_action_worker.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write the worker job**

```python
# src/ai_sdr/worker/jobs/execute_action.py
"""execute_action — arq worker job (FE-03c §6.2).

Cross-tenant: bypasses RLS for the action_executions lookup (worker is
trusted, same pattern as scan_talks). Re-sets tenant context before any
tenant-scoped reads (e.g. secrets via SopsLoader, tenant.yaml loader).

State machine:
  pending --enqueue--> executing --success--> success
                          |
                          +-- exception (attempts < 3) --> raise (arq retries)
                          +-- exception (attempts >= 3) --> failed (terminal)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.flowengine.actions.factory import build_action_adapter
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 3


async def execute_action(ctx: dict[str, Any], execution_id_str: str) -> None:
    execution_id = UUID(execution_id_str)
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        # Bypass RLS for cross-tenant read (worker is trusted).
        await session.execute(text("SET LOCAL row_security = off"))

        repo = ActionExecutionRepository(session)
        execution = await repo.mark_executing(execution_id)
        if execution is None:
            logger.info("action.execution_not_found id=%s", execution_id)
            await session.commit()
            return

        # Re-set tenant context for any tenant-scoped reads.
        await set_tenant_context(session, execution.tenant_id)
        await session.commit()  # Lock + status flip is in its own tx.

        try:
            tenant = await _load_tenant_by_id(execution.tenant_id)
            adapter = build_action_adapter(execution.adapter_name, tenant)
            result = await adapter.execute(
                handler=execution.handler,
                params=execution.params_resolved,
            )
        except Exception as exc:  # noqa: BLE001 — adapter contract: raises on failure
            is_terminal = execution.attempts >= MAX_ATTEMPTS
            await session.execute(text("SET LOCAL row_security = off"))
            # Re-fetch under lock to update the row.
            refresh = await repo.mark_executing(execution_id)  # re-locks
            if refresh is None:
                logger.info("action.execution_not_found_during_fail id=%s", execution_id)
                await session.commit()
                return
            await repo.mark_failed(refresh, error=str(exc), terminal=is_terminal)
            await session.commit()
            if is_terminal:
                logger.error(
                    "action.failed execution=%s attempts=%d err=%s",
                    execution_id, refresh.attempts, exc,
                )
                return
            logger.warning(
                "action.retry execution=%s attempts=%d err=%s",
                execution_id, refresh.attempts, exc,
            )
            raise

        # Success path.
        await session.execute(text("SET LOCAL row_security = off"))
        refresh = await repo.mark_executing(execution_id)
        if refresh is None:
            logger.info("action.execution_not_found_during_success id=%s", execution_id)
            await session.commit()
            return
        await repo.mark_success(refresh, external_id=result.external_id)
        await session.commit()
        logger.info(
            "action.executed execution=%s attempts=%d external_id=%s",
            execution_id, refresh.attempts, result.external_id,
        )


async def _load_tenant_by_id(tenant_id: uuid.UUID) -> Any:
    """Wrapper around the tenant_loader so tests can patch this symbol."""
    from pathlib import Path

    from ai_sdr.settings import get_settings
    from ai_sdr.tenant_loader.loader import TenantLoader

    loader = TenantLoader(Path(get_settings().tenants_dir))
    return await loader.load_by_id(tenant_id)
```

Note on `mark_executing` reuse: the repo method re-locks the row each call, which is fine for the success/fail update path. If `mark_executing` increments attempts again, that's actually a bug — let me adjust the repo OR have separate methods. Simpler: split — make `mark_executing` the lock+executing+attempts++ only used once, and add a `_lock_for_update(execution_id)` for the update phase. To keep T10/T12 self-contained without refactoring T10, we use this slight clunkiness: the post-execute re-fetch only marks success/fail without further increment because `mark_failed`/`mark_success` set status directly without touching `attempts`. The `mark_executing` re-call in the catch block IS a bug — fix it inline:

```python
# Replace the catch and success paths with direct SELECT-FOR-UPDATE re-fetch,
# bypassing the repo's mark_executing helper (which would re-increment attempts):

from sqlalchemy import select
from ai_sdr.models.action_execution import ActionExecution

async def _refetch_locked(session, execution_id):
    return (
        await session.execute(
            select(ActionExecution)
            .where(ActionExecution.id == execution_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
```

Final worker body:

```python
# (replace the worker body above with this version)

async def execute_action(ctx: dict[str, Any], execution_id_str: str) -> None:
    execution_id = UUID(execution_id_str)
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        await session.execute(text("SET LOCAL row_security = off"))
        repo = ActionExecutionRepository(session)
        execution = await repo.mark_executing(execution_id)
        if execution is None:
            logger.info("action.execution_not_found id=%s", execution_id)
            await session.commit()
            return
        await set_tenant_context(session, execution.tenant_id)
        await session.commit()

        try:
            tenant = await _load_tenant_by_id(execution.tenant_id)
            adapter = build_action_adapter(execution.adapter_name, tenant)
            result = await adapter.execute(
                handler=execution.handler,
                params=execution.params_resolved,
            )
        except Exception as exc:
            await session.execute(text("SET LOCAL row_security = off"))
            refresh = await _refetch_locked(session, execution_id)
            if refresh is None:
                await session.commit()
                return
            is_terminal = refresh.attempts >= MAX_ATTEMPTS
            await repo.mark_failed(refresh, error=str(exc), terminal=is_terminal)
            await session.commit()
            if is_terminal:
                logger.error(
                    "action.failed execution=%s attempts=%d err=%s",
                    execution_id, refresh.attempts, exc,
                )
                return
            logger.warning(
                "action.retry execution=%s attempts=%d err=%s",
                execution_id, refresh.attempts, exc,
            )
            raise

        await session.execute(text("SET LOCAL row_security = off"))
        refresh = await _refetch_locked(session, execution_id)
        if refresh is None:
            await session.commit()
            return
        await repo.mark_success(refresh, external_id=result.external_id)
        await session.commit()
        logger.info(
            "action.executed execution=%s attempts=%d external_id=%s",
            execution_id, refresh.attempts, result.external_id,
        )


async def _refetch_locked(session: AsyncSession, execution_id: UUID):
    from sqlalchemy import select

    from ai_sdr.models.action_execution import ActionExecution

    return (
        await session.execute(
            select(ActionExecution)
            .where(ActionExecution.id == execution_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
```

Update the unit test to reflect `_refetch_locked` instead of re-invoking `mark_executing`. The relevant tests should patch `_refetch_locked` similarly:

```python
# In the test file, also patch _refetch_locked:
with patch(
    "ai_sdr.worker.jobs.execute_action._refetch_locked",
    AsyncMock(return_value=fake_execution),
):
    ...
```

Adjust all 4 tests in `test_execute_action_worker.py` to add this patch context manager around the success/failure paths (the lookup-not-found test doesn't need it since `mark_executing` returns None before `_refetch_locked` is reached).

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_execute_action_worker.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/execute_action.py tests/unit/test_execute_action_worker.py
git commit -m "feat(fe03c t12): execute_action worker job — retry + state machine"
```

---

## Phase 6: Wiring

### Task 13: Wire `dispatch_actions` in `post_processing` + register worker job

**Files:**
- Modify: `src/ai_sdr/flowengine/post_processing.py`
- Modify: `src/ai_sdr/worker/main.py`
- Test: `tests/unit/test_post_processing_dispatches_actions.py`
- Test: `tests/unit/test_worker_main_registers_execute_action.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_post_processing_dispatches_actions.py
"""apply_decision invokes dispatch_actions between merge and close lifecycle (FE-03c Task 13)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_apply_decision_calls_dispatch_actions():
    """apply_decision must invoke dispatch_actions after merging collected_fields."""
    # Build minimal mocks for the apply_decision call site.
    # We patch dispatch_actions and the repository at the import level used by post_processing.
    with patch(
        "ai_sdr.flowengine.post_processing.dispatch_actions",
        AsyncMock(),
    ) as mock_dispatch:
        # Import inside the patch so the symbol resolution sees the mock.
        from ai_sdr.flowengine.post_processing import apply_decision

        session = MagicMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        talk = SimpleNamespace(
            id=uuid4(), tenant_id=uuid4(), lead_id=uuid4(),
            treeflow_id="tf", turn_count=0, last_message_at=None,
            status="active", closed_at=None, closed_reason=None, closed_by=None,
            requires_review_reason=None,
        )
        node = SimpleNamespace(id="n1", on_collected=[])
        treeflow = SimpleNamespace(
            nodes=[node],
            talk_lifecycle=None,
        )
        state = SimpleNamespace(
            collected={}, extracted_facts={}, current_node="n1",
            active_treatment=None, objections_handled=[],
        )
        decision = SimpleNamespace(
            collected_fields={"demo_data": "2026-06-13"},
            extracted_facts={}, response_text="ok",
            response_format="text", next_node=None,
            objection=None, objection_resolved=False,
            off_topic=False, escalation_reason=None,
        )

        # Patch the heavier dependencies we don't care about for this test.
        with patch(
            "ai_sdr.flowengine.post_processing.apply_objection_state",
            return_value=SimpleNamespace(
                changes_treatment=False, new_active_treatment=None,
                appended_objection_history=[], events=[],
                requires_review_reason=None,
            ),
        ), patch(
            "ai_sdr.flowengine.post_processing.apply_contradiction_heuristic",
            return_value=(decision, []),
        ), patch(
            "ai_sdr.flowengine.post_processing.detect_implicit_transition",
            return_value=[],
        ), patch(
            "ai_sdr.flowengine.post_processing.evaluate_completion_rule",
            return_value=None,
        ), patch(
            "ai_sdr.flowengine.post_processing.TalkFlowStateRepository"
        ) as MockRepo, patch(
            "ai_sdr.flowengine.post_processing._emit_events"
        ):
            MockRepo.return_value.append_message = AsyncMock()

            await apply_decision(
                session,
                talk=talk,
                state=state,
                decision=decision,
                resolved_target_node="n1",
                now=datetime.now(timezone.utc),
                treeflow=treeflow,
            )

        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.await_args.kwargs
        assert kwargs["state"] is state
        assert kwargs["decision"] is decision
        assert kwargs["node_spec"].id == "n1"
```

```python
# tests/unit/test_worker_main_registers_execute_action.py
"""worker.main registers execute_action in WorkerSettings.functions (FE-03c Task 13)."""

from __future__ import annotations

from ai_sdr.worker.jobs.execute_action import execute_action
from ai_sdr.worker.main import WorkerSettings


def test_execute_action_in_functions_list():
    assert execute_action in WorkerSettings.functions
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_post_processing_dispatches_actions.py tests/unit/test_worker_main_registers_execute_action.py -v
```

Expected: both fail (`dispatch_actions` not imported in post_processing; `execute_action` not in functions list).

- [ ] **Step 3: Wire dispatch_actions in post_processing**

Edit `src/ai_sdr/flowengine/post_processing.py`:

1. Add to imports near the top:

```python
from ai_sdr.flowengine.actions.dispatcher import dispatch_actions
from ai_sdr.repositories.action_execution_repository import ActionExecutionRepository
```

2. Locate the existing block (around line 102 after the FE-03b merge) and insert dispatch right after the `extracted_facts` merge:

```python
# AFTER (existing):
#     if decision.extracted_facts:
#         merged_facts = dict(state.extracted_facts)
#         merged_facts.update(decision.extracted_facts)
#         state.extracted_facts = merged_facts
#         flag_modified(state, "extracted_facts")

# INSERT BEFORE the `# 5. Apply state delta` block:

# FE-03c §6.1: on_collected action dispatch.
# Runs BEFORE current_node update so node_spec lookup matches the node
# where the LLM emitted the collected_fields.
node_spec_for_actions = next(
    (n for n in treeflow.nodes if n.id == state.current_node),
    None,
)
if node_spec_for_actions is not None and getattr(node_spec_for_actions, "on_collected", []):
    lead_for_actions = await session.get(_get_lead_model(), talk.lead_id)
    if lead_for_actions is not None:
        action_repo = ActionExecutionRepository(session)
        await dispatch_actions(
            session=session,
            repo=action_repo,
            enqueue=_make_action_enqueue(),
            state=state,
            decision=decision,
            node_spec=node_spec_for_actions,
            talk=talk,
            lead=lead_for_actions,
        )
```

3. Add helpers at module level:

```python
# At module bottom or below imports:

def _get_lead_model():
    """Lazy import to avoid circular dependency at module load."""
    from ai_sdr.models.lead import Lead
    return Lead


def _make_action_enqueue():
    """Build the enqueue callable. In production this resolves to arq pool;
    in tests it's typically patched. Module-level so tests can patch it.
    """
    from ai_sdr.worker.queue import enqueue_execute_action
    return enqueue_execute_action
```

Create the queue helper:

```python
# src/ai_sdr/worker/queue.py — create if not exists
"""arq enqueue helpers."""
from __future__ import annotations

from arq.connections import ArqRedis, RedisSettings, create_pool

from ai_sdr.settings import get_settings

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue_execute_action(execution_id_str: str) -> None:
    pool = await _get_pool()
    await pool.enqueue_job("execute_action", execution_id_str)
```

4. Wire worker:

Edit `src/ai_sdr/worker/main.py`:

```python
# Add to imports:
from ai_sdr.worker.jobs.execute_action import execute_action

# Update functions list:
class WorkerSettings:
    """arq looks up class attributes by name."""

    functions = [process_lead_inbox, execute_action]  # add execute_action
    # ... rest unchanged
```

5. Make sure `flowengine.actions` is imported at startup so adapters register. Add to `ai_sdr/__init__.py` or to the API/worker startup. Simplest: import at top of `post_processing.py`:

```python
# Top of post_processing.py (with other imports):
import ai_sdr.flowengine.actions  # noqa: F401 — side-effect: register adapters
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_post_processing_dispatches_actions.py tests/unit/test_worker_main_registers_execute_action.py -v
uv run pytest tests/unit/test_post_processing_completion_close.py -v  # FE-03b: must still pass
uv run pytest tests/unit/ -v  # full unit suite must still pass
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/post_processing.py src/ai_sdr/worker/main.py src/ai_sdr/worker/queue.py tests/unit/test_post_processing_dispatches_actions.py tests/unit/test_worker_main_registers_execute_action.py
git commit -m "feat(fe03c t13): wire dispatch_actions in post_processing + register worker job"
```

---

## Phase 7: Integration tests + close-out

### Task 14: Integration tests (skip-friendly reference contracts)

**Files:**
- Create: `tests/integration/test_action_dispatcher_end_to_end.py`
- Create: `tests/integration/test_action_executions_rls.py`

- [ ] **Step 1: Write the integration tests**

```python
# tests/integration/test_action_dispatcher_end_to_end.py
"""E2E reference contract: turn collects field → action enqueues → worker executes (FE-03c Task 14).

Skip-friendly: depends on the `run_turn_harness` and `tenant_factory` fixtures
that don't exist locally — will skip with "fixture not found" until the
harness module lands (post-FE-03c). Reference contract documents the
expected end-to-end behavior.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_turn_collecting_field_enqueues_action(
    async_session, run_turn_harness, fake_llm_polite, tenant_factory,
):
    """Lead emits demo_data → action_executions row pending → worker → success."""
    fake_llm = fake_llm_polite(
        response_text="Beleza, agendado.",
        collected_fields={"demo_data": "2026-06-13"},
    )
    await run_turn_harness.send_inbound("quarta às 14h")
    await run_turn_harness.run(llm=fake_llm)

    # Action row exists, status eventually 'success' (worker processes it).
    rows = await run_turn_harness.fetch_action_executions()
    assert len(rows) == 1
    assert rows[0]["field"] == "demo_data"
    assert rows[0]["status"] == "success"
    assert rows[0]["external_id"].startswith("fake-schedule_event-")


async def test_same_value_twice_skips_duplicate(
    async_session, run_turn_harness, fake_llm_polite,
):
    """Two turns both emit same demo_data → only 1 action_executions row."""
    fake_llm = fake_llm_polite(
        response_text="ok",
        collected_fields={"demo_data": "2026-06-13"},
    )
    await run_turn_harness.send_inbound("quarta às 14h")
    await run_turn_harness.run(llm=fake_llm)
    await run_turn_harness.send_inbound("confirmado quarta")
    await run_turn_harness.run(llm=fake_llm)

    rows = await run_turn_harness.fetch_action_executions()
    assert len(rows) == 1
```

```python
# tests/integration/test_action_executions_rls.py
"""RLS isolation on action_executions (FE-03c Task 14)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_reads(db_session: AsyncSession) -> None:
    """Tenant A inserts a row; tenant B reading sees zero rows."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    for tid in (tenant_a, tenant_b):
        await db_session.execute(
            text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
            {"i": tid, "s": f"t-{tid.hex[:8]}", "n": "t"},
        )

    # Insert under tenant A context.
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    talk_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_a},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_a},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            " treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:tid, :ten, :lid, 'tf', :tfv, 'active', 'ai', now())"
        ),
        {"tid": talk_id, "ten": tenant_a, "lid": lead_id, "tfv": tfv_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO action_executions "
            "(tenant_id, talk_id, node_id, field, value_hash, "
            " adapter_name, handler, params_resolved, status) "
            "VALUES (:ten, :tid, 'n', 'f', 'h', 'logging', 'x', '{}'::jsonb, 'pending')"
        ),
        {"ten": tenant_a, "tid": talk_id},
    )

    # Switch to tenant B; should NOT see tenant A's row.
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_b)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM action_executions"))
    assert result.scalar() == 0

    # Back to tenant A.
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM action_executions"))
    assert result.scalar() == 1
```

- [ ] **Step 2: Verify tests collect (locally they'll skip due to missing fixtures for E2E; RLS test runs only on VPS)**

```bash
uv run pytest tests/integration/test_action_dispatcher_end_to_end.py tests/integration/test_action_executions_rls.py --collect-only 2>&1 | tail -10
```

Expected: 3 tests collected (collection success, may error/skip at fixture resolution — that's the skip-friendly pattern).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_action_dispatcher_end_to_end.py tests/integration/test_action_executions_rls.py
git commit -m "test(fe03c t14): integration reference contracts (skip-friendly)"
```

---

### Task 15: CLAUDE.md update + final lint/format/type/test pass + close-out

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the FE-03c section to CLAUDE.md**

Append after the FE-03b section (between `## Messaging (Plano 5)` and `## HITL Console (Plano 11)`, or in document order matching existing sections):

```markdown
## Actions (FE-03c)

- TreeFlow YAMLs declaram side-effects inline em `NodeSpec.on_collected`:
  ```yaml
  nodes:
    - id: agendamento_demo
      collect:
        - field: demo_data
          required: true
      on_collected:
        - field: demo_data
          adapter: logging
          handler: schedule_event
          params:
            title: "Demo {{ collected.nome }}"
            duration_minutes: 30
  ```
- Action dispara **assíncrono via worker arq** depois que LLM emite `collected_fields[field]`. Não bloqueia run_turn.
- **Idempotência**: UNIQUE `(talk_id, field, value_hash)` em `action_executions` — mesma coleta (mesmo valor) = skip; correção (valor muda) = nova action.
- **Templating**: Jinja2 SandboxedEnvironment com `StrictUndefined`. Contexto exposto: `collected`, `extracted_facts`, `lead.{id, whatsapp_e164, external_label}`, `talk.{id, treeflow_id, turn_count}`. `tenant_id` é deliberadamente **não exposto**. Render rola no dispatcher (sync), `params_resolved` no DB já é o final.
- **Adapter framework**: ABC `ActionAdapter` + registry (`@register` decorator) + factory (`build_action_adapter`). Espelha `messaging/` (pattern conceitual idêntico). Adicionar novo adapter:
  1. Subclasse `ActionAdapter`, set `name`, implementa `execute(handler, params)`.
  2. Decora com `@register`.
  3. Importa em `flowengine/actions/__init__.py` (side-effect).
- **Adapters incluídos no MVP**: `logging` (fake/test, retorna fake id determinístico). Adapters reais (Google Calendar, HubSpot, etc) ficam pra plano dedicado de produção.
- **Falha**: 3 retries com backoff exponencial (5s, 30s). Após terminal: `status='failed'`, `last_error` (1000 chars). Sem replay automático no MVP — operador investiga via SQL e abre plano se for sistêmico.
- **Bump de version** obrigatório ao adicionar/mudar `on_collected` num TreeFlow já publicado (Plan 2 rule).
- **Events estruturados** (structlog): `action.enqueued`, `action.executed`, `action.retry`, `action.failed`, `action.dispatch.skipped_duplicate`, `action.dispatch.template_render_failed`, `action.dispatch.unknown_adapter`, `action.execution_not_found`.
- **Queries operacionais**:
  ```sql
  -- Taxa de falha por adapter (24h)
  SELECT adapter_name,
         COUNT(*) FILTER (WHERE status='failed') * 100.0 / COUNT(*) AS pct_failed
  FROM action_executions
  WHERE created_at > now() - interval '1 day'
  GROUP BY adapter_name;

  -- Stuck jobs (worker crash?)
  SELECT * FROM action_executions
  WHERE status='executing' AND updated_at < now() - interval '5 minutes';
  ```
- **Cross-tenant worker**: `execute_action` faz `SET LOCAL row_security = off` pra lookup, depois `set_tenant_context` pra reads tenant-scoped (secrets, tenant.yaml).
- **Wipe pra dev fresh**: `docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "TRUNCATE action_executions;"`.
```

- [ ] **Step 2: Run full quality gate**

```bash
make lint
make format
make type
make test-unit
```

Expected: all green. If `make format` reformats files, stage them as part of the same commit.

- [ ] **Step 3: VPS validation (when Docker not available locally)**

On the VPS worktree:

```bash
ssh vps-nova 'export PATH=$PATH:/root/.local/bin && cd /root/PeSDR-fe03a && git pull --ff-only && uv run alembic upgrade head && uv run pytest tests/integration/test_migration_0028_action_executions.py tests/integration/test_action_executions_rls.py -v'
```

Expected: alembic applies 0028; 5/5 tests pass (4 from migration test + 1 from RLS test). The E2E reference contract test will skip (`fixture 'run_turn_harness' not found`) — expected per the skip-friendly pattern.

- [ ] **Step 4: Close-out commit**

```bash
git add CLAUDE.md
git commit -m "chore(fe03c): close-out — all 15 tasks landed

FE-03c MVP complete. Action pipeline + adapter framework + fake logging
adapter wired through post_processing and worker. Migration 0028 applied
on VPS, RLS verified, constraint accepts/rejects validated. Closes the
FE-03 refactor cycle (FE-03a + FE-03b + FE-03c)."
```

- [ ] **Step 5: Push branch + open PR**

```bash
git push origin dev/nicolas-fe03c-actions-adapter-framework
gh pr create --base dev/nicolas-fe03b-humanization-close-lifecycle \
  --title "FE-03c — on_collected actions + adapter framework MVP" \
  --body "$(cat <<'EOF'
## Summary

Closes the FE-03 refactor cycle. Adds the async on_collected action pipeline
and the plug-and-play ActionAdapter framework (mirror of messaging/).

- 15 tasks, all green
- Migration 0028 (action_executions table + RLS + constraints)
- 9 new modules in flowengine/actions/
- 1 worker job (execute_action) registered in WorkerSettings
- Single source of truth for the status enum (Literal + get_args)
- Fake `logging` adapter for tests
- VPS-validated: alembic upgrade clean, RLS isolates cross-tenant

## Spec & plan

- Spec: docs/superpowers/specs/2026-06-12-fe03c-actions-adapter-framework-design.md
- Plan: docs/superpowers/plans/2026-06-12-fe03c-actions-adapter-framework.md

## Test plan

- [x] make test-unit green (all)
- [x] make lint + format + type clean
- [x] VPS: alembic 0028 applied, integration constraint + RLS tests pass
- [ ] E2E reference contracts pending run_turn_harness fixture (future plan)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist (run after writing the plan, fix inline)

- [x] **Spec coverage:**
  - §3 architecture → Tasks 5–13 cover all 9 new modules + 2 extensions.
  - §4 YAML schema → Task 4 (OnCollectedAction + loader).
  - §5 DB schema → Tasks 2 + 3 (migration + ORM).
  - §6 runtime → Tasks 10 (repo) + 11 (dispatcher) + 12 (worker) + 13 (wiring).
  - §7 framework → Tasks 5 (ABC) + 6 (registry) + 7 (factory) + 9 (fake).
  - §8 templating → Task 8.
  - §9 observability → events emitted throughout 11/12; CLAUDE.md doc in T15.
  - §10 brechas → A1–A10 covered by dispatcher tests (T11) + worker tests (T12) + templating tests (T8) + loader tests (T4).
  - §11 test plan → unit + 2 integration files (T14).

- [x] **Placeholder scan:** no TBDs, no "implement later" — code blocks all complete.

- [x] **Type consistency:**
  - `OnCollectedAction` shape: `field, adapter, handler, params` consistent across T4, T11, T13.
  - `ActionResult.external_id` nullable in T5, used as nullable in T9 (fake returns str), T10 (repo `external_id=None` default), T12 (worker passes `result.external_id`).
  - `ActionExecutionRepository` methods consistent: `insert_pending`, `mark_executing`, `mark_success`, `mark_failed` across T10, T11, T12.

- [x] **Open question resolved during plan write-up:** repo's `mark_executing` was being re-called from worker post-execute, which would double-increment `attempts`. Replaced with `_refetch_locked` helper inline in T12.
