# Pilot Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ai-sdr pilot --tenant <slug>` — a multi-turn REPL CLI that drives the real worker pipeline (`process_lead_inbox` via arq) using `FakeMessagingAdapter`, so Avelum-style internal validation can happen without Meta Cloud API credentials.

**Architecture:** Single new module `src/ai_sdr/cli/pilot.py` (~200 LOC) with pure helpers (TDD-friendly), one async DB helper, one async REPL loop with injectable I/O (testable), and a typer entrypoint that wires together a `create_async_engine`-owned session + an arq `create_pool`. The loop reads stdin, INSERTs an `InboundMessageRow`, enqueues `process_lead_inbox`, polls `outbound_messages` for a new row (500ms interval, 30s timeout), and prints `body_text`. End signals (`lead.status='pending_assignment'`, `talkflow.status='cold'`, `:quit`, Ctrl+C, failed audit row, timeout) exit cleanly with the appropriate code.

**Tech Stack:** existing — typer, rich, sqlalchemy async, arq, asyncio. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-06-01-pilot-harness-design.md`](../specs/2026-06-01-pilot-harness-design.md). Read §3 (UX) and §4 (architecture) before starting.

---

## File Structure

```
src/ai_sdr/cli/
├── pilot.py                                NEW (~200 LOC)
└── app.py                                  MODIFIED — register pilot_app

tests/unit/
└── test_pilot_cli.py                       NEW

tests/integration/
└── test_pilot_loop.py                      NEW
```

The harness is entirely additive — no worker, audit, registry, or model files change.

**Module layout inside `pilot.py`:**

1. Imports + `pilot_app = typer.Typer(...)` + `console = Console()`
2. **Pure helpers** (testable without DB):
   - `generate_whatsapp_e164() -> str`
   - `resolve_treeflow(tenants_dir, slug, requested) -> str`
   - `format_status_line(lead, talkflow, turn_count) -> str`
3. **Async DB helpers**:
   - `poll_for_outbound(session, lead_id, after, max_seconds, interval_seconds) -> OutboundMessage | None`
   - `_seed_session(session, tenants_dir, slug, treeflow_id, from_address) -> tuple[Tenant, Lead, TalkFlow]`
4. **Loop**:
   - `_run_loop(session_factory, pool, tenant, lead, talkflow, input_fn, output_fn) -> int`
5. **Entry point**:
   - `pilot(tenant, treeflow, from_address)` — typer command, wraps `asyncio.run(_main(...))`
   - `_main(tenant_slug, treeflow_id, from_address)` — owns engine + pool lifecycle, calls `_seed_session` + `_run_loop`

---

## Prerequisites

- Branch `dev/nicolas-pilot-harness` already exists (created during brainstorming) and the spec is already committed there.
- Working tree clean; on branch `dev/nicolas-pilot-harness`.
- VPS is irrelevant for this plan — all work is local. Integration test runs against local Postgres (port 15432 per project memory).

---

## Task 1: Skeleton + pure helpers

**Files:**
- Create: `src/ai_sdr/cli/pilot.py`
- Create: `tests/unit/test_pilot_cli.py`

**Design:** Per spec §4.1 — 3 pure helpers with no I/O. `generate_whatsapp_e164` returns `+5511990` + 6 random hex chars (length 13). `resolve_treeflow` scans `tenants/<slug>/treeflows/*.yaml`: explicit `--treeflow` wins; otherwise exactly 1 file → return its stem; 0 or >1 → raise with message. `format_status_line` returns one-line summary used by `:status`.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_pilot_cli.py`:

```python
"""Pure helpers for ai-sdr pilot — no DB, no network, no asyncio."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_sdr.cli.pilot import (
    format_status_line,
    generate_whatsapp_e164,
    resolve_treeflow,
)


# --- generate_whatsapp_e164 ---


def test_generate_whatsapp_e164_format() -> None:
    n = generate_whatsapp_e164()
    assert re.fullmatch(r"\+5511990[0-9a-f]{6}", n), n
    assert len(n) == 13


def test_generate_whatsapp_e164_is_random() -> None:
    # 100 samples; collision probability negligible (16**6 = 16M combinations)
    samples = {generate_whatsapp_e164() for _ in range(100)}
    assert len(samples) >= 99


# --- resolve_treeflow ---


def test_resolve_treeflow_explicit_flag_wins(tmp_path: Path) -> None:
    # Even if directory has many files, explicit flag is returned as-is.
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "a.yaml").write_text("")
    (tmp_path / "s" / "treeflows" / "b.yaml").write_text("")
    assert resolve_treeflow(tmp_path, "s", "explicit") == "explicit"


def test_resolve_treeflow_single_file_auto_pick(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "qualificacao.yaml").write_text("")
    assert resolve_treeflow(tmp_path, "s", None) == "qualificacao"


def test_resolve_treeflow_no_files_raises(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    with pytest.raises(FileNotFoundError) as exc:
        resolve_treeflow(tmp_path, "s", None)
    assert "treeflows" in str(exc.value)


def test_resolve_treeflow_dir_missing_raises(tmp_path: Path) -> None:
    # tenants/<slug>/treeflows/ does not exist at all
    with pytest.raises(FileNotFoundError):
        resolve_treeflow(tmp_path, "missing-slug", None)


def test_resolve_treeflow_multiple_files_requires_flag(tmp_path: Path) -> None:
    (tmp_path / "s" / "treeflows").mkdir(parents=True)
    (tmp_path / "s" / "treeflows" / "a.yaml").write_text("")
    (tmp_path / "s" / "treeflows" / "b.yaml").write_text("")
    with pytest.raises(ValueError) as exc:
        resolve_treeflow(tmp_path, "s", None)
    msg = str(exc.value)
    assert "a" in msg and "b" in msg
    assert "--treeflow" in msg


# --- format_status_line ---


def test_format_status_line_includes_all_fields() -> None:
    lead = SimpleNamespace(id="2d404cfb-9f60-48c1-b741-9db641f4072e", status="active")
    talkflow = SimpleNamespace(status="active")
    line = format_status_line(lead, talkflow, turn_count=4)
    assert "2d404cfb" in line
    assert "active" in line
    assert "turns=4" in line
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py -v`

Expected: collection error / `ModuleNotFoundError: No module named 'ai_sdr.cli.pilot'`.

- [ ] **Step 3: Create the skeleton + implement helpers**

Create `src/ai_sdr/cli/pilot.py`:

```python
"""ai-sdr pilot — multi-turn REPL driving the worker pipeline via FakeAdapter.

Drives process_lead_inbox end-to-end with a real LLM and real DB/Redis,
but no Meta Cloud API. Each REPL turn: INSERT inbound row → enqueue arq job
→ poll outbound_messages for a new row → print body_text. End signals
(handoff, cold, failed audit, timeout, :quit, Ctrl+C) exit cleanly.

Scope and non-goals: see docs/superpowers/specs/2026-06-01-pilot-harness-design.md.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow


pilot_app = typer.Typer(help="Drive the worker pipeline via terminal — fake adapter, real LLM.")
console = Console()


# --- Pure helpers (no I/O) ---


def generate_whatsapp_e164() -> str:
    """Random E.164-style number for a fresh pilot lead. Format: +5511990 + 6 hex."""
    return f"+5511990{secrets.token_hex(3)}"


def resolve_treeflow(tenants_dir: Path, slug: str, requested: str | None) -> str:
    """Determine which treeflow id to seed.

    Explicit `requested` always wins. Otherwise scan
    `tenants/<slug>/treeflows/*.yaml`: if exactly 1 file, return its stem;
    if 0 or >1, raise with a helpful message.
    """
    if requested:
        return requested
    tf_dir = tenants_dir / slug / "treeflows"
    if not tf_dir.is_dir():
        raise FileNotFoundError(
            f"treeflows directory not found: {tf_dir}. "
            f"Ensure tenants/{slug}/treeflows/ exists with at least one .yaml file."
        )
    files = sorted(tf_dir.glob("*.yaml"))
    if len(files) == 1:
        return files[0].stem
    if len(files) == 0:
        raise FileNotFoundError(
            f"No treeflow YAML in {tf_dir}. "
            f"Add one or pass --treeflow <id>."
        )
    names = ", ".join(f.stem for f in files)
    raise ValueError(
        f"Multiple treeflows in {tf_dir}: {names}. "
        f"Pass --treeflow <id> to disambiguate."
    )


def format_status_line(lead: "Lead", talkflow: "TalkFlow", turn_count: int) -> str:
    """One-line summary printed by the `:status` REPL command."""
    return (
        f"lead_id={str(lead.id)[:8]}… "
        f"lead.status={lead.status} · "
        f"talkflow.status={talkflow.status} · "
        f"turns={turn_count}"
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py -v`

Expected: all 9 tests pass.

- [ ] **Step 5: Lint clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check src/ai_sdr/cli/pilot.py tests/unit/test_pilot_cli.py`

Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add src/ai_sdr/cli/pilot.py tests/unit/test_pilot_cli.py
git commit -m "$(cat <<'EOF'
feat(pilot t1): skeleton + pure helpers (e164, resolve_treeflow, status)

generate_whatsapp_e164 produces "+5511990<6 hex>" (random per lead).
resolve_treeflow auto-picks the only .yaml in treeflows/, or raises
with a clear hint when 0 or >1 files exist (or directory missing).
format_status_line is the one-liner for :status. All pure functions,
unit-tested, no DB or async involved.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: poll_for_outbound async helper

**Files:**
- Modify: `src/ai_sdr/cli/pilot.py`
- Modify: `tests/unit/test_pilot_cli.py`

**Design:** Per spec §4.2 data-flow diagram. Polls `outbound_messages` with `lead_id == X AND created_at > T`, ordered by `created_at ASC`, limited to 1. Sleeps `interval_seconds` between checks. Returns the first row that appears, or `None` after `max_seconds`. Used both by the loop (real DB) and unit-testable via a mocked session.

- [ ] **Step 1: Write the failing unit tests**

Add to `tests/unit/test_pilot_cli.py`:

```python
import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from ai_sdr.cli.pilot import poll_for_outbound


def _mock_session_returning(rows_per_call: list[object | None]) -> MagicMock:
    """Build a session whose .execute() returns scalar_one_or_none() = rows_per_call[i]
    on call i. Use None to simulate 'no row yet'."""
    s = MagicMock()
    calls = iter(rows_per_call)

    async def execute(_stmt):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=next(calls))
        return result

    s.execute = execute
    return s


async def test_poll_returns_first_row() -> None:
    sentinel = MagicMock()
    session = _mock_session_returning([sentinel])
    row = await poll_for_outbound(
        session,
        lead_id=uuid.uuid4(),
        after=datetime.now(UTC),
        max_seconds=1.0,
        interval_seconds=0.01,
    )
    assert row is sentinel


async def test_poll_returns_none_on_timeout() -> None:
    # Always returns None — should hit timeout and return None.
    session = _mock_session_returning([None] * 1000)
    row = await poll_for_outbound(
        session,
        lead_id=uuid.uuid4(),
        after=datetime.now(UTC),
        max_seconds=0.1,
        interval_seconds=0.01,
    )
    assert row is None


async def test_poll_waits_then_finds() -> None:
    # First 3 calls return None, then a row.
    sentinel = MagicMock()
    session = _mock_session_returning([None, None, None, sentinel])
    row = await poll_for_outbound(
        session,
        lead_id=uuid.uuid4(),
        after=datetime.now(UTC),
        max_seconds=1.0,
        interval_seconds=0.01,
    )
    assert row is sentinel
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py::test_poll_returns_first_row -v`

Expected: `ImportError: cannot import name 'poll_for_outbound' from 'ai_sdr.cli.pilot'`.

- [ ] **Step 3: Implement poll_for_outbound**

Add to `src/ai_sdr/cli/pilot.py` (after the pure helpers section):

```python
import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.outbound_message import OutboundMessage


# --- Async DB helpers ---


async def poll_for_outbound(
    session: AsyncSession,
    lead_id: uuid.UUID,
    after: datetime,
    max_seconds: float = 30.0,
    interval_seconds: float = 0.5,
) -> OutboundMessage | None:
    """Poll outbound_messages for the first row with created_at > after.

    Returns the row when found, or None after max_seconds. The caller is
    responsible for setting tenant RLS context on the session before calling.
    """
    elapsed = 0.0
    while elapsed < max_seconds:
        result = await session.execute(
            select(OutboundMessage)
            .where(OutboundMessage.lead_id == lead_id)
            .where(OutboundMessage.created_at > after)
            .order_by(OutboundMessage.created_at.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds
    return None
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py -v`

Expected: all tests pass (8 from Task 1 + 3 new = 11).

- [ ] **Step 5: Lint clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check src/ai_sdr/cli/pilot.py tests/unit/test_pilot_cli.py`

Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add src/ai_sdr/cli/pilot.py tests/unit/test_pilot_cli.py
git commit -m "$(cat <<'EOF'
feat(pilot t2): poll_for_outbound — wait for a new audit row

Async helper that polls outbound_messages with lead_id=X AND
created_at>T, sleeping interval_seconds between checks. Returns the
first matching row or None after max_seconds (default 30s @ 500ms).
Caller owns the session and RLS context. 3 unit tests via a mocked
session: immediate hit, timeout, eventual hit after waiting.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_seed_session` async setup helper

**Files:**
- Modify: `src/ai_sdr/cli/pilot.py`
- Create: `tests/integration/test_pilot_loop.py` (test setup section only)

**Design:** Per spec §4.3 startup sequence. Loads the existing tenant by slug (fails fast if absent), loads-or-creates a `TreeflowVersion` by `(tenant_id, treeflow_id, content_hash)`, creates a fresh `Lead` (status='active') with the given `whatsapp_e164`, creates a `TalkFlow` linked to both. Commits and returns the three ORM rows for downstream use. RLS context must be set by the caller before this helper runs.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_pilot_loop.py` with just the seed test for now:

```python
"""Pilot harness — DB-touching helpers + end-to-end loop."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ai_sdr.cli.pilot import _seed_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


# Minimal valid treeflow YAML for the harness — same shape as production.
_YAML = (
    "id: pilot_test\n"
    "version: 1.0.0\n"
    "entry_node: n1\n"
    "nodes: {n1: {prompt: hi}}\n"
)


async def test_seed_session_creates_lead_and_talkflow(
    db_session, tmp_path: Path
) -> None:
    # Set up: tenant in DB, treeflow YAML on disk.
    tenant = Tenant(slug=f"pilot_{uuid.uuid4().hex[:6]}", display_name="Pilot")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    tenant_out, lead_out, tf_out = await _seed_session(
        db_session,
        tenants_dir=tmp_path,
        slug=tenant.slug,
        treeflow_id="pilot_test",
        from_address="+5511990abc123",
    )

    assert tenant_out.id == tenant.id
    assert lead_out.whatsapp_e164 == "+5511990abc123"
    assert lead_out.status == "active"
    assert tf_out.lead_id == lead_out.id
    assert tf_out.treeflow_version_id is not None

    # Verify TreeflowVersion was created with the expected content.
    tv = (
        await db_session.execute(
            select(TreeflowVersion).where(TreeflowVersion.id == tf_out.treeflow_version_id)
        )
    ).scalar_one()
    assert tv.treeflow_id == "pilot_test"
    assert tv.content_yaml == _YAML


async def test_seed_session_reuses_existing_treeflow_version(
    db_session, tmp_path: Path
) -> None:
    # When YAML content matches an existing TreeflowVersion's content_hash, reuse it.
    tenant = Tenant(slug=f"pilot_{uuid.uuid4().hex[:6]}", display_name="Pilot")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    # First call creates the TreeflowVersion.
    _, _, tf1 = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990aaa111",
    )
    # Second call must find the same TreeflowVersion (no duplicate).
    _, _, tf2 = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990bbb222",
    )
    assert tf1.treeflow_version_id == tf2.treeflow_version_id


async def test_seed_session_fails_when_tenant_missing(
    db_session, tmp_path: Path
) -> None:
    with pytest.raises(ValueError) as exc:
        await _seed_session(
            db_session, tenants_dir=tmp_path, slug="does-not-exist",
            treeflow_id="x", from_address="+5511990aaaaaa",
        )
    assert "does-not-exist" in str(exc.value)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/integration/test_pilot_loop.py -v`

Expected: `ImportError: cannot import name '_seed_session'` (or skip if local DB not running — that's OK, the failure is wired).

- [ ] **Step 3: Implement `_seed_session`**

Add to `src/ai_sdr/cli/pilot.py` (after `poll_for_outbound`):

```python
import hashlib

from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


async def _seed_session(
    session: AsyncSession,
    *,
    tenants_dir: Path,
    slug: str,
    treeflow_id: str,
    from_address: str,
) -> tuple[Tenant, Lead, TalkFlow]:
    """Set up a fresh pilot session: tenant lookup, treeflow_version, lead, talkflow.

    Caller is responsible for setting RLS context BEFORE this runs (the
    helper does its own commits but does not switch tenant). Returns
    (tenant, lead, talkflow) for the caller to use in the REPL loop.

    Raises:
        ValueError: tenant slug not found in DB.
        FileNotFoundError: treeflow YAML file missing.
    """
    # 1. Look up tenant.
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError(
            f"tenant '{slug}' not in DB. Add it via psql before piloting: "
            f"INSERT INTO tenants (slug, display_name) VALUES ('{slug}', '<name>');"
        )

    # 2. Load YAML, compute content_hash, find-or-create TreeflowVersion.
    yaml_path = tenants_dir / slug / "treeflows" / f"{treeflow_id}.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"treeflow YAML not found: {yaml_path}")
    content = yaml_path.read_text()
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    tv = (
        await session.execute(
            select(TreeflowVersion).where(
                TreeflowVersion.tenant_id == tenant.id,
                TreeflowVersion.treeflow_id == treeflow_id,
                TreeflowVersion.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if tv is None:
        tv = TreeflowVersion(
            tenant_id=tenant.id,
            treeflow_id=treeflow_id,
            version="pilot",
            content_hash=content_hash,
            content_yaml=content,
        )
        session.add(tv)
        await session.flush()

    # 3. Create fresh lead + talkflow.
    lead = Lead(tenant_id=tenant.id, whatsapp_e164=from_address, status="active")
    session.add(lead)
    await session.flush()

    talkflow = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    session.add(talkflow)
    await session.commit()
    return tenant, lead, talkflow
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/integration/test_pilot_loop.py -v`

Expected: 3 tests pass (assuming local DB up). If DB unavailable, tests fail with a connection error — that's a fixture issue, not a code defect. Verify the unit tests still pass:

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py -v`

Expected: 12 tests pass.

- [ ] **Step 5: Lint clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check src/ai_sdr/cli/pilot.py tests/integration/test_pilot_loop.py`

Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add src/ai_sdr/cli/pilot.py tests/integration/test_pilot_loop.py
git commit -m "$(cat <<'EOF'
feat(pilot t3): _seed_session — tenant/treeflow/lead/talkflow setup

Async helper that runs once at pilot startup: validates the tenant
exists in DB (clear error if not), loads-or-creates a TreeflowVersion
keyed by content_hash (so re-runs reuse the same version row instead
of churning), creates a fresh Lead + TalkFlow with the supplied
whatsapp_e164. Caller owns RLS context. 3 integration tests cover
the happy path, treeflow reuse, and missing-tenant error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_run_loop` — the REPL with injectable I/O

**Files:**
- Modify: `src/ai_sdr/cli/pilot.py`
- Modify: `tests/integration/test_pilot_loop.py`

**Design:** Per spec §3 (UX) + §4.2 (data flow per turn) + §4.4 (end signal detection order). The loop is the centerpiece. `input_fn` and `output_fn` are injectable so tests can drive it without a terminal; production wraps `input` and `console.print`. End-signal detection happens in the exact order specified in §4.4. Handles `:quit`, `:status`, empty line, normal text. Turn counter increments only on successful agent reply.

- [ ] **Step 1: Add the loop integration test (failing)**

Append to `tests/integration/test_pilot_loop.py`:

```python
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.cli.pilot import _run_loop
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.outbound_message import OutboundMessage


async def _make_eco_pool(session_factory, tenant_id, lead_id):
    """Build a MagicMock pool whose enqueue_job simulates the worker by
    reading the latest inbound and writing an eco outbound row. Returns
    the pool and a list that captures every text the 'agent' produced."""
    pool = MagicMock()
    agent_replies: list[str] = []

    async def fake_enqueue(name, *args, **kwargs):
        # name == "process_lead_inbox"; args == (str(tenant.id), str(lead.id))
        async with session_factory() as db:
            await set_tenant_context(db, tenant_id)
            latest = (
                await db.execute(
                    select(InboundMessageRow)
                    .where(InboundMessageRow.lead_id == lead_id)
                    .order_by(InboundMessageRow.received_at.desc())
                    .limit(1)
                )
            ).scalar_one()
            reply = f"eco: {latest.text}"
            agent_replies.append(reply)
            tf = (
                await db.execute(
                    select(TalkFlow).where(TalkFlow.lead_id == lead_id)
                )
            ).scalar_one()
            db.add(
                OutboundMessage(
                    tenant_id=tenant_id,
                    talkflow_id=tf.id,
                    lead_id=lead_id,
                    provider="fake",
                    message_type="text",
                    body_text=reply,
                    status="sent",
                    external_id=f"fake_{uuid.uuid4().hex[:8]}",
                    triggered_by="inbound",
                    sent_at=datetime.now(UTC),
                )
            )
            await db.commit()

    pool.enqueue_job = fake_enqueue
    return pool, agent_replies


async def test_run_loop_quit_immediately(db_session, tmp_path) -> None:
    # User types :quit on the very first prompt — no turns happen.
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990quit01",
    )

    # session_factory wraps a fresh session per loop tick.
    # Use the test fixture's engine via the existing db_session machinery.
    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, _ = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter([":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert any("encerrado" in o for o in outputs)


async def test_run_loop_two_turn_eco(db_session, tmp_path) -> None:
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990two001",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, agent_replies = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter(["Oi", "Tudo bem?", ":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert agent_replies == ["eco: Oi", "eco: Tudo bem?"]
    # Each agent reply appears in outputs, prefixed with "agente:".
    assert sum(1 for o in outputs if o.startswith("agente:")) == 2


async def test_run_loop_handles_status_command(db_session, tmp_path) -> None:
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990stat01",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pool, _ = await _make_eco_pool(sf, tenant.id, lead.id)

    outputs: list[str] = []
    inputs = iter([":status", ":quit"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    # :status output contains the marker fields.
    assert any("turns=0" in o for o in outputs)
    assert any("lead.status=active" in o for o in outputs)


async def test_run_loop_handoff_ends_conversation(db_session, tmp_path) -> None:
    """When lead.status becomes 'pending_assignment', loop ends with code 0."""
    tenant = Tenant(slug=f"p_{uuid.uuid4().hex[:6]}", display_name="P")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    _, lead, talkflow = await _seed_session(
        db_session, tenants_dir=tmp_path, slug=tenant.slug,
        treeflow_id="pilot_test", from_address="+5511990hand01",
    )

    sf = async_sessionmaker(db_session.bind, expire_on_commit=False)
    # Custom pool: write outbound AND flip lead.status to pending_assignment.
    pool = MagicMock()

    async def handoff_enqueue(name, *args, **kwargs):
        async with sf() as db:
            await set_tenant_context(db, tenant.id)
            tf = (
                await db.execute(select(TalkFlow).where(TalkFlow.lead_id == lead.id))
            ).scalar_one()
            db.add(
                OutboundMessage(
                    tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
                    provider="fake", message_type="text",
                    body_text="Vou te conectar com um humano.", status="sent",
                    external_id=f"fake_{uuid.uuid4().hex[:8]}",
                    triggered_by="inbound", sent_at=datetime.now(UTC),
                )
            )
            db_lead = (
                await db.execute(select(Lead).where(Lead.id == lead.id))
            ).scalar_one()
            db_lead.status = "pending_assignment"
            await db.commit()

    pool.enqueue_job = handoff_enqueue

    outputs: list[str] = []
    inputs = iter(["Quero falar com humano"])

    code = await _run_loop(
        session_factory=sf,
        pool=pool,
        tenant=tenant,
        lead=lead,
        talkflow=talkflow,
        input_fn=lambda _prompt: next(inputs),
        output_fn=outputs.append,
    )

    assert code == 0
    assert any("pending_assignment" in o for o in outputs)
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/integration/test_pilot_loop.py -v`

Expected: `ImportError: cannot import name '_run_loop'`.

- [ ] **Step 3: Implement `_run_loop`**

Add to `src/ai_sdr/cli/pilot.py`:

```python
from collections.abc import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow


# --- Loop ---


async def _run_loop(
    *,
    session_factory: async_sessionmaker,
    pool,  # arq pool; duck-typed so tests can pass a MagicMock with .enqueue_job
    tenant: Tenant,
    lead: Lead,
    talkflow: TalkFlow,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> int:
    """REPL loop. Returns the exit code per spec §4.4.

    Test-friendly: input_fn(prompt) -> str (production wraps stdin's input)
    and output_fn(line) -> None (production wraps console.print). Per-turn
    flow per spec §4.2; end signals checked in the order from §4.4.
    """
    turn_count = 0

    while True:
        user_text = input_fn("> ").strip()

        if user_text == ":quit":
            output_fn("[encerrado]")
            return 0

        if user_text == ":status":
            async with session_factory() as db:
                await set_tenant_context(db, tenant.id)
                refreshed_lead = (
                    await db.execute(select(Lead).where(Lead.id == lead.id))
                ).scalar_one()
                refreshed_tf = (
                    await db.execute(select(TalkFlow).where(TalkFlow.id == talkflow.id))
                ).scalar_one()
                output_fn(format_status_line(refreshed_lead, refreshed_tf, turn_count))
            continue

        if not user_text:
            continue

        # 1. INSERT inbound, COMMIT, capture timestamp.
        before_send = datetime.now(UTC)
        async with session_factory() as db:
            await set_tenant_context(db, tenant.id)
            db.add(
                InboundMessageRow(
                    tenant_id=tenant.id,
                    provider="fake",
                    external_id=f"pilot_{uuid.uuid4().hex}",
                    lead_id=lead.id,
                    from_address=lead.whatsapp_e164,
                    text=user_text,
                    received_at=datetime.now(UTC),
                    raw={},
                )
            )
            await db.commit()

        # 2. Enqueue arq job. (Production: real arq pool. Tests: MagicMock that
        # simulates the worker by writing the outbound row directly.)
        await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))

        # 3. Poll for the new outbound row + check end signals.
        async with session_factory() as db:
            await set_tenant_context(db, tenant.id)
            row = await poll_for_outbound(db, lead.id, before_send)

            if row is None:
                output_fn(
                    "[timeout — worker não respondeu em 30s. "
                    "Verifica `docker compose ps` e `docker compose logs worker`.]"
                )
                return 1

            if row.status == "failed":
                output_fn(
                    f"[falha no processamento — {row.error_detail}. "
                    f"Verifica logs do worker.]"
                )
                return 1

            # End-signal check order per spec §4.4:
            refreshed_lead = (
                await db.execute(select(Lead).where(Lead.id == lead.id))
            ).scalar_one()
            refreshed_tf = (
                await db.execute(select(TalkFlow).where(TalkFlow.id == talkflow.id))
            ).scalar_one()

            output_fn(f"agente: {row.body_text}")
            turn_count += 1

            if refreshed_lead.status == "pending_assignment":
                output_fn(
                    "[lead encaminhado pro operador humano — status=pending_assignment]"
                )
                return 0
            if refreshed_tf.status == "cold":
                output_fn("[talkflow esfriou — sem mais respostas]")
                return 0
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/integration/test_pilot_loop.py -v`

Expected: 4 loop tests pass + 3 seed tests pass = 7 integration tests.

Also re-verify unit tests:

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py -v`

Expected: 11 unit tests pass.

- [ ] **Step 5: Lint clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check src/ai_sdr/cli/pilot.py tests/integration/test_pilot_loop.py`

Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add src/ai_sdr/cli/pilot.py tests/integration/test_pilot_loop.py
git commit -m "$(cat <<'EOF'
feat(pilot t4): _run_loop — REPL with injectable I/O

The loop: read user text, INSERT inbound, enqueue arq job, poll
outbound_messages, print agent reply, check end signals. input_fn
and output_fn are injectable so tests can drive it without a
terminal. End-signal detection follows the spec §4.4 order:
timeout → failed audit → handoff → cold → continue. :quit and
:status handled inline. 4 integration tests via a stub pool that
simulates the worker by writing eco outbound rows directly: quit
immediately, two-turn eco, :status command, handoff detection.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `pilot` typer command + register sub-app

**Files:**
- Modify: `src/ai_sdr/cli/pilot.py`
- Modify: `src/ai_sdr/cli/app.py`

**Design:** Per spec §4.3 and §4.5. The typer command wraps `asyncio.run(_main(...))`. `_main` owns engine + pool lifecycle: creates `create_async_engine`, builds `async_sessionmaker`, opens an arq `create_pool`, calls `_seed_session` + `_run_loop`, disposes both cleanly even on KeyboardInterrupt. Header printed via rich. The sub-app gets registered in `cli/app.py` next to the other CLI groups.

- [ ] **Step 1: Implement `_main` + `pilot` command**

Append to `src/ai_sdr/cli/pilot.py`:

```python
import asyncio
from typing import Annotated

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import create_async_engine

from ai_sdr.settings import get_settings


# --- Entry point ---


@pilot_app.command("pilot")
def pilot(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug (required)")],
    treeflow: Annotated[
        str | None,
        typer.Option("--treeflow", help="Treeflow id (yaml basename, no .yaml)"),
    ] = None,
    from_address: Annotated[
        str | None,
        typer.Option("--from-address", help="Lead whatsapp_e164 (default: random)"),
    ] = None,
) -> None:
    """Run a multi-turn pilot conversation against the live worker pipeline."""
    asyncio.run(_main(tenant, treeflow, from_address))


async def _main(
    tenant_slug: str, treeflow_arg: str | None, from_address_arg: str | None
) -> None:
    settings = get_settings()
    tenants_dir = Path(settings.tenants_dir)

    # Resolve treeflow id (filesystem only — no DB yet).
    try:
        treeflow_id = resolve_treeflow(tenants_dir, tenant_slug, treeflow_arg)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from e

    from_address = from_address_arg or generate_whatsapp_e164()

    engine = create_async_engine(settings.database_url, future=True)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    pool = None
    try:
        # Seed and grab the rows.
        async with sf() as db:
            try:
                tenant_row, lead, talkflow = await _seed_session(
                    db,
                    tenants_dir=tenants_dir,
                    slug=tenant_slug,
                    treeflow_id=treeflow_id,
                    from_address=from_address,
                )
            except (ValueError, FileNotFoundError) as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1) from e

        # Open the arq pool (Redis must be reachable).
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

        # Header.
        console.print(
            f"[cyan]Piloto {tenant_slug} · lead {from_address} · "
            f"treeflow={treeflow_id}[/cyan]"
        )
        console.print("[dim](:quit ou Ctrl+C pra sair, :status pra ver estado)[/dim]")

        # Run the REPL. KeyboardInterrupt is caught here for clean teardown.
        try:
            exit_code = await _run_loop(
                session_factory=sf,
                pool=pool,
                tenant=tenant_row,
                lead=lead,
                talkflow=talkflow,
                input_fn=input,
                output_fn=console.print,
            )
        except KeyboardInterrupt:
            console.print("\n[dim][encerrado][/dim]")
            exit_code = 0

        raise typer.Exit(exit_code)
    finally:
        if pool is not None:
            await pool.aclose()
        await engine.dispose()
```

- [ ] **Step 2: Register `pilot_app` in `cli/app.py`**

Open `src/ai_sdr/cli/app.py` and add the import + `add_typer` line. The post-Plan-10 file looks like this (line numbers may differ slightly):

```python
"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.follow_ups import follow_ups_app
from ai_sdr.cli.leads import leads_app
from ai_sdr.cli.outbound import outbound_app
from ai_sdr.cli.pilot import pilot_app                       # NEW
from ai_sdr.cli.reindex_kb import reindex_kb_app
from ai_sdr.cli.simulate import simulate
from ai_sdr.cli.users import users_app
from ai_sdr.cli.worker import worker

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)
app.add_typer(reindex_kb_app, name="reindex-kb")
app.add_typer(leads_app, name="leads")
app.add_typer(follow_ups_app, name="follow-ups")
app.add_typer(outbound_app, name="outbound")
app.add_typer(pilot_app, name="pilot")                       # NEW
app.add_typer(users_app, name="users")
app.command(name="worker")(worker)


if __name__ == "__main__":  # pragma: no cover
    app()
```

The exact insertion: `from ai_sdr.cli.pilot import pilot_app` alphabetically between `outbound` and `reindex_kb`, and `app.add_typer(pilot_app, name="pilot")` after `outbound` (just before `users`).

- [ ] **Step 3: Smoke import + smoke help**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run python -c "from ai_sdr.cli.app import app; print('ok')"`

Expected: prints `ok`.

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ai-sdr pilot pilot --help`

Expected: shows usage with `--tenant`, `--treeflow`, `--from-address` options.

> Note: the command path is `ai-sdr pilot pilot` because the sub-app is registered as `pilot` and the inner command is also `pilot`. This is the same shape as `ai-sdr outbound list` (sub-app=`outbound`, command=`list`). If undesirable, future cleanup can register the sub-app without the wrapper — but it's consistent with the other groups, so leave it for now.

- [ ] **Step 4: Lint + format clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check src/ai_sdr/cli/pilot.py src/ai_sdr/cli/app.py && uv run ruff format --check src/ai_sdr/cli/pilot.py src/ai_sdr/cli/app.py`

Expected: "All checks passed!" + no format diffs.

- [ ] **Step 5: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add src/ai_sdr/cli/pilot.py src/ai_sdr/cli/app.py
git commit -m "$(cat <<'EOF'
feat(pilot t5): pilot entry point + register sub-app in cli/app

`ai-sdr pilot pilot --tenant <slug>` wraps asyncio.run(_main). _main
owns the engine + arq pool lifecycle and guarantees clean teardown
(pool.aclose + engine.dispose) on KeyboardInterrupt or typer.Exit.
Header printed via rich; REPL uses stdin's input + console.print as
the production I/O pair.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Final integration test — full loop wired through `_main`

**Files:**
- Modify: `tests/integration/test_pilot_loop.py`

**Design:** Most of the REPL is already covered by Task 4's `_run_loop` tests. This task adds one end-to-end test that exercises `_main` itself — including engine creation, pool open/close, header print, full lifecycle. Uses `monkeypatch` to swap `create_pool` for a MagicMock and `input` for an iterator. Verifies the loop completes and engines are disposed.

- [ ] **Step 1: Add the end-to-end test (failing)**

Append to `tests/integration/test_pilot_loop.py`:

```python
async def test_main_runs_end_to_end_with_stubbed_pool(
    db_session, tmp_path, monkeypatch
) -> None:
    """End-to-end smoke: _main creates engine, opens pool, runs loop, tears down."""
    from ai_sdr.cli import pilot as pilot_mod

    tenant = Tenant(slug=f"e2e_{uuid.uuid4().hex[:6]}", display_name="E2E")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    (tmp_path / tenant.slug / "treeflows").mkdir(parents=True)
    (tmp_path / tenant.slug / "treeflows" / "pilot_test.yaml").write_text(_YAML)
    await db_session.commit()

    # Point settings.tenants_dir at our tmp_path.
    settings = pilot_mod.get_settings()
    monkeypatch.setattr(settings, "tenants_dir", str(tmp_path))

    # Stub create_pool — production opens a Redis connection; tests don't need it.
    pool_inst = MagicMock()
    pool_inst.enqueue_job = AsyncMock()
    pool_inst.aclose = AsyncMock()

    async def fake_create_pool(*args, **kwargs):
        return pool_inst

    monkeypatch.setattr(pilot_mod, "create_pool", fake_create_pool)

    # Inject :quit so the loop exits immediately.
    inputs = iter([":quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))

    with pytest.raises(typer.Exit) as exc:
        await pilot_mod._main(tenant.slug, None, "+5511990e2e000")

    assert exc.value.exit_code == 0
    pool_inst.aclose.assert_awaited()  # cleanup happened
```

Add `import typer` to the test imports if not present.

- [ ] **Step 2: Run the test to confirm it passes**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/integration/test_pilot_loop.py::test_main_runs_end_to_end_with_stubbed_pool -v`

Expected: PASS.

Also re-verify the suite:

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run pytest tests/unit/test_pilot_cli.py tests/integration/test_pilot_loop.py -v`

Expected: 11 unit + 8 integration = 19 tests pass.

- [ ] **Step 3: Lint clean**

Run: `cd /Users/nicolasamaral/dev/PeSDR && uv run ruff check tests/integration/test_pilot_loop.py`

Expected: "All checks passed!".

- [ ] **Step 4: Commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git add tests/integration/test_pilot_loop.py
git commit -m "$(cat <<'EOF'
test(pilot t6): end-to-end _main test with stubbed Redis pool

Validates the full lifecycle: _main creates engine + sessionmaker,
opens arq pool (stubbed), runs the seed → _run_loop, tears down on
typer.Exit. monkeypatched create_pool returns a MagicMock so the
test doesn't need a live Redis. Confirms pool.aclose was awaited
(no resource leak on the exit path).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Close-out

**Files:** none (close-out steps).

- [ ] **Step 1: Run the full lint + format + type + test gates**

```bash
cd /Users/nicolasamaral/dev/PeSDR && \
  uv run ruff check . && \
  uv run ruff format --check . && \
  uv run mypy src && \
  uv run pytest tests/unit -q
```

Expected: all green. Unit test count should be 304 (baseline post-hardening) + 11 (Task 1+2 unit tests) = 315.

If `make format` introduces changes (unlikely — we lint-clean each task), commit them as `chore(pilot): pre-close-out ruff format`.

- [ ] **Step 2: Push the branch**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git push origin dev/nicolas-pilot-harness
```

- [ ] **Step 3: Open the PR**

```bash
cd /Users/nicolasamaral/dev/PeSDR && gh pr create --base dev/nicolas --head dev/nicolas-pilot-harness \
  --title "Pilot harness: drive worker pipeline via FakeAdapter" \
  --body "$(cat <<'EOF'
## Summary

Adds `ai-sdr pilot --tenant <slug>` — a multi-turn REPL that drives the real `process_lead_inbox` arq job via `FakeMessagingAdapter`, so internal validation (Avelum dogfooding, customer demos) can happen end-to-end without Meta Cloud API credentials.

**Spec:** [`docs/superpowers/specs/2026-06-01-pilot-harness-design.md`](../blob/dev/nicolas-pilot-harness/docs/superpowers/specs/2026-06-01-pilot-harness-design.md)
**Plan:** [`docs/superpowers/plans/2026-06-01-pilot-harness.md`](../blob/dev/nicolas-pilot-harness/docs/superpowers/plans/2026-06-01-pilot-harness.md)

### What it adds

- **CLI**: `ai-sdr pilot pilot --tenant <slug> [--treeflow <id>] [--from-address <e164>]`
- **REPL commands**: `:quit`, `:status`, empty line (skip), any other text (lead message)
- **End signals**: timeout (30s), failed audit row, handoff (`lead.status='pending_assignment'`), cold (`talkflow.status='cold'`)
- **Random lead per session**: `+5511990<6 hex chars>`
- **Treeflow auto-pick** if exactly 1 YAML in `tenants/<slug>/treeflows/`

### What it does NOT add

(Explicit non-goals from spec §2)

- No time simulation (`:fast-forward`)
- No failure injection
- No multi-tenant per session
- No debug panel (use `ai-sdr outbound list` for that)
- No tenant config bootstrapping (must exist before piloting)

### Tests

- 11 unit tests (pure helpers, poll behavior)
- 8 integration tests (seed, REPL loop, end-to-end with stubbed pool)
- Total +19 tests; new unit baseline 315 (was 304).

### Manual validation after merge

Once an Avelum tenant exists in DB + YAML, run the 3 in-scope scenarios from spec §5.3:

1. Lead novo → greeting + qualification question
2. Objeção de preço → classifier triggers + guardrails enforce whitelist
3. Handoff humano → loop detects `pending_assignment` and exits cleanly

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Tag the close-out commit**

```bash
cd /Users/nicolasamaral/dev/PeSDR && git commit --allow-empty -m "$(cat <<'EOF'
chore(pilot): close-out — 6 tasks landed

ai-sdr pilot REPL drives process_lead_inbox via FakeAdapter for
end-to-end internal validation without Meta Cloud API. Pure helpers,
DB helpers, REPL loop, entry point + sub-app registration, full
end-to-end integration test. 19 new tests (11 unit + 8 integration);
unit suite baseline now 315 (was 304).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Notes for plan execution

- **Tenant prerequisite for manual validation.** The harness requires `tenants/<slug>/` with `tenant.yaml`, `secrets.enc.yaml` (decryptable, with a valid LLM key), and at least one treeflow YAML — plus the tenant row in DB. Configuring Avelum is separate work; until that lands, manual validation is blocked but tests still pass.
- **Integration tests need local Postgres up.** The `db_session` fixture in `tests/conftest.py` (from P5) expects Postgres on port 15432. If unavailable locally, integration tests fail at fixture setup, not at the assertion — that's a fixture issue, not a code defect.
- **Worker container code does not change.** The pilot enqueues `process_lead_inbox` exactly as a real webhook would. The only test stubs are at the pool layer, not at the worker job itself. In production this means the moment Avelum's tenant is configured, the harness works against the live worker without any redeploy.
- **`arq` is already a dependency** — no `pyproject.toml` change.
- **No new ENV vars.** Reuses `DATABASE_URL`, `REDIS_URL`, `TENANTS_DIR` from `Settings`.
