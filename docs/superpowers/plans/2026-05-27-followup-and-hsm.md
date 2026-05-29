# Follow-up scheduler + HSM Templates Implementation Plan (Plano 9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add proactive follow-up scheduling + WhatsApp HSM template support. After this plan, when a lead goes silent the PeSDR worker fires HSM templates from the TreeFlow's `follow_up.sequence` at the configured intervals (resetting when lead responds, marking `talkflow.status='cold'` after `max_attempts`). When `send_text` hits `WindowExpiredError` (from Plan 5), the worker auto-falls back to the tenant's `reengagement_template`.

**Architecture:** New `follow_up_jobs` table (RLS, tenant-scoped) tracks scheduled attempts; an `arq.cron` scanner runs every 60s, picks due rows, and dispatches via per-lead `pg_advisory_lock` (same lock the inbound worker uses, so the two paths serialize). `MessagingAdapter` gains `send_template(to, ref, lang, params)` as an additive contract method; `WhatsAppCloudAPIAdapter` implements it via Meta's `POST /messages type=template` endpoint with the same `tenacity` retry stack and `_classify_error` taxonomy from Plan 5. TreeFlow YAML schema gains `follow_up: {enabled, max_attempts, sequence: [{after, template_ref, language, params}]}` with ISO-8601 durations; template params render via Jinja2 `SandboxedEnvironment` against `collected/lead/tenant`. TalkFlow gains 3 columns (`last_agent_message_at`, `last_lead_message_at`, `follow_up_attempt_number`) — these drive timing, race-belt, and counter.

**Tech Stack additions:** `isodate>=0.6` (ISO-8601 duration parser, ~12KB). HSM templates referenced by name only — Meta Business Manager remains source-of-truth for the actual approved content.

**Spec:** [`docs/superpowers/specs/2026-05-27-followup-and-hsm-design.md`](../specs/2026-05-27-followup-and-hsm-design.md). Read §3 (não-objetivos), §5 (data model), §6 (algoritmos críticos), §7 (CLI), §9 (testing) before starting.

---

## File Structure

```
src/ai_sdr/
├── follow_up/                              # NEW package — shared helpers
│   ├── __init__.py
│   ├── duration.py                         # NEW: parse_duration(iso) → timedelta
│   ├── jinja.py                            # NEW: render_params via SandboxedEnvironment
│   ├── treeflow_loader.py                  # NEW: load_treeflow_follow_up(db, talkflow)
│   └── scheduler.py                        # NEW: schedule_next_followup, cancel_pending_for_lead, mark_cold_if_exhausted
│
├── messaging/
│   ├── base.py                             # MODIFIED: add abstract send_template method
│   ├── whatsapp_cloud.py                   # MODIFIED: implement send_template (HSM POST)
│   └── fake.py                             # MODIFIED: implement send_template (records to sent_templates)
│
├── models/
│   ├── follow_up_job.py                    # NEW: FollowUpJob ORM
│   ├── talkflow.py                         # MODIFIED: 3 new columns
│   └── __init__.py                         # MODIFIED: re-export FollowUpJob
│
├── schemas/
│   ├── treeflow_yaml.py                    # MODIFIED: FollowUpStep + FollowUpConfig + follow_up field
│   └── tenant_yaml.py                      # MODIFIED: ReengagementTemplate + reengagement_template on MessagingConfig
│
├── worker/
│   ├── main.py                             # MODIFIED: cron_jobs=[cron(follow_up_scanner, ...)]
│   └── jobs/
│       ├── inbound.py                      # MODIFIED: 3 changes (cancel-on-inbound, cold-reactivate, schedule-after-send, WindowExpired recovery)
│       └── follow_up_scanner.py            # NEW: scanner + _fire_follow_up
│
├── cli/
│   ├── follow_ups.py                       # NEW: ai-sdr follow-ups {list,cancel,dry-run}
│   └── app.py                              # MODIFIED: register follow_ups_app

migrations/versions/
└── 0010_follow_up_and_talkflow_columns.py  # NEW

tenants/example/
├── tenant.yaml                             # MODIFIED: messaging.reengagement_template (commented opt-in)
└── treeflows/example.yaml                  # MODIFIED: follow_up section for dev/QA

pyproject.toml                              # MODIFIED: add isodate
CLAUDE.md                                   # MODIFIED: new "Follow-up + HSM templates (Plano 9)" section

tests/
├── unit/
│   ├── test_follow_up_duration.py          # NEW
│   ├── test_follow_up_jinja.py             # NEW (sandbox + filters)
│   ├── test_follow_up_config_schema.py     # NEW
│   ├── test_reengagement_template_schema.py
│   ├── test_messaging_base_send_template.py  # NEW (ABC enforcement)
│   ├── test_fake_send_template.py          # NEW
│   ├── test_whatsapp_send_template_payload.py  # NEW
│   └── test_follow_ups_cli.py              # NEW
└── integration/
    ├── test_follow_up_jobs_model.py
    ├── test_follow_up_scanner_basic.py
    ├── test_follow_up_scanner_race_belt.py
    ├── test_follow_up_scanner_serializes.py
    ├── test_follow_up_full_lifecycle.py
    ├── test_follow_up_cancellation_on_inbound.py
    ├── test_window_expired_recovery.py
    ├── test_window_expired_no_template_fallback.py
    ├── test_follow_up_recipient_unreachable.py
    └── test_adapter_compliance.py          # MODIFIED: add send_template tests
```

**Layout notes:**
- `follow_up/` is a NEW package. Splits cleanly from `messaging/` because the helpers operate on TreeFlow config + state, not the messaging contract. Worker job and CLI both import from here.
- `messaging/base.py` extension is **additive** — existing impls compile but fail to instantiate until `send_template` is implemented (Tasks 8, 9 handle both impls).
- Migration 0010 lands BOTH `follow_up_jobs` table AND the 3 new TalkFlow columns in one migration — they're conceptually one feature; splitting just for granularity adds revision overhead.

---

## Prerequisites (delta from Plan 5)

Plan 5's prereqs (Docker, uv, age, sops, ANTHROPIC_API_KEY/OPENAI_API_KEY) still apply. **No new ENV vars** required.

The VPS runs Postgres on port 15432 already. Plan 9 migration applies cleanly on top of 0009 (Plano 11) when both ship — they touch disjoint tables. If P11 hasn't landed yet, P9 migration 0010 still applies (no FK to users/user_tenant_access from `follow_up_jobs`).

### Shared test fixtures

`tests/conftest.py` from Plano 5 (with `db_session` + `app` fixtures, NullPool, session-scoped event loop) is reused as-is.

### VPS notes

After deploying, run `uv run alembic upgrade head` once to apply 0010. Then restart the worker container: `docker compose up -d --build worker` — the new cron job registers on startup.

---

## Task 1: Add `isodate` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dependency**

Open `pyproject.toml` and locate the `dependencies` array. Insert `"isodate>=0.6",` alphabetically (between `httpx` and `jinja2` if present, otherwise between `httpx` and `langchain-*`):

```toml
dependencies = [
    # ... existing entries ...
    "httpx>=0.28",
    "isodate>=0.6",
    "jinja2>=3.1",            # may or may not exist yet; leave if it does
    # ... rest ...
]
```

- [ ] **Step 2: Lock + install**

Run: `uv lock && uv sync`

Expected: lock file updated. No errors.

- [ ] **Step 3: Smoke-import**

Run:
```bash
uv run python -c "import isodate; print(isodate.parse_duration('PT24H'))"
```

Expected: prints `1 day, 0:00:00` (= `datetime.timedelta(days=1)`).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(plan9 t1): add isodate dependency

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Migration 0010 — `follow_up_jobs` + TalkFlow columns

**Files:**
- Create: `migrations/versions/0010_follow_up_and_talkflow_columns.py`

**Design:** One migration adds the `follow_up_jobs` table (with RLS + indexes) AND the 3 new columns on `talkflows`. Both changes are part of the same feature; splitting just complicates revision tracking.

The two partial indexes are non-negotiable: `ix_follow_up_jobs_due` is hit on every scanner run; `ix_follow_up_jobs_lead_pending` is hit on every inbound (cancel-pending bulk update).

- [ ] **Step 1: Create the migration**

Create `migrations/versions/0010_follow_up_and_talkflow_columns.py`:

```python
"""follow_up_jobs table (with RLS + partial indexes) + TalkFlow timing columns

Revision ID: 0010_follow_up_and_talkflow_columns
Revises: 0009_users_and_access
Create Date: 2026-05-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_follow_up_and_talkflow_columns"
down_revision = "0009_users_and_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- follow_up_jobs table ---
    op.create_table(
        "follow_up_jobs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talkflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_external_id", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talkflow_id"], ["talkflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'error')",
            name="ck_follow_up_jobs_status",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_follow_up_jobs_attempt_positive",
        ),
    )
    op.create_index(
        "ix_follow_up_jobs_due",
        "follow_up_jobs",
        ["scheduled_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_follow_up_jobs_lead_pending",
        "follow_up_jobs",
        ["lead_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.execute("ALTER TABLE follow_up_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE follow_up_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY follow_up_jobs_tenant_isolation ON follow_up_jobs
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )

    # --- talkflows columns ---
    op.add_column(
        "talkflows",
        sa.Column("last_agent_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "talkflows",
        sa.Column("last_lead_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "talkflows",
        sa.Column(
            "follow_up_attempt_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("talkflows", "follow_up_attempt_number")
    op.drop_column("talkflows", "last_lead_message_at")
    op.drop_column("talkflows", "last_agent_message_at")
    op.execute("DROP POLICY IF EXISTS follow_up_jobs_tenant_isolation ON follow_up_jobs")
    op.drop_index("ix_follow_up_jobs_lead_pending", table_name="follow_up_jobs")
    op.drop_index("ix_follow_up_jobs_due", table_name="follow_up_jobs")
    op.drop_table("follow_up_jobs")
```

- [ ] **Step 2: Verify revision shape locally**

Run: `uv run python -c "from alembic.script import ScriptDirectory; from alembic.config import Config; sd = ScriptDirectory.from_config(Config('alembic.ini')); print([s.revision for s in sd.walk_revisions()])"`

Expected: list includes `'0010_follow_up_and_talkflow_columns'` as the tip after `'0009_users_and_access'` (if P11 is already merged) or after `'0008_talkflows_lead_id_fk'` (if P11 hasn't merged yet — adjust `down_revision` accordingly when consolidating).

- [ ] **Step 3: Skip local apply (Docker on VPS)**

Controller pushes + runs migration on VPS during validation.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0010_follow_up_and_talkflow_columns.py
git commit -m "$(cat <<'EOF'
feat(plan9 t2): migration 0010 — follow_up_jobs + TalkFlow timing columns

New table (tenant-scoped RLS, 2 partial indexes for scanner + bulk
cancel) plus 3 columns on talkflows (last_agent_message_at,
last_lead_message_at, follow_up_attempt_number). Single migration —
all part of the same feature.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `FollowUpJob` ORM model + `TalkFlow` model update

**Files:**
- Create: `src/ai_sdr/models/follow_up_job.py`
- Modify: `src/ai_sdr/models/talkflow.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `tests/integration/test_follow_up_jobs_model.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_follow_up_jobs_model.py`:

```python
"""FollowUpJob ORM — RLS isolation, FK cascades, check constraints, partial indexes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"f_{uuid.uuid4().hex[:6]}", display_name="F")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tenant, tf, lead


async def test_create_follow_up_job_defaults(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    job = FollowUpJob(
        tenant_id=tenant.id,
        talkflow_id=tf.id,
        lead_id=lead.id,
        attempt_number=1,
        scheduled_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(job)
    await db_session.commit()
    assert job.status == "pending"
    assert job.fired_at is None
    assert job.created_at is not None


async def test_check_constraint_rejects_bad_status(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC), status="weird",
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_check_constraint_rejects_zero_attempt(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=0, scheduled_at=datetime.now(UTC),
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_rls_blocks_cross_tenant_read(db_session) -> None:
    tenant_a, tf_a, lead_a = await _seed(db_session)
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant_a.id, talkflow_id=tf_a.id, lead_id=lead_a.id,
        attempt_number=1, scheduled_at=datetime.now(UTC),
    ))
    await db_session.commit()

    # Switch to a fresh tenant — should see nothing
    tenant_b = Tenant(slug=f"b_{uuid.uuid4().hex[:6]}", display_name="B")
    db_session.add(tenant_b)
    await db_session.commit()
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(FollowUpJob))).scalars().all()
    assert rows == []


async def test_lead_cascade_delete_removes_jobs(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await db_session.delete(lead)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(select(FollowUpJob))).scalars().all()
    assert rows == []


async def test_talkflow_new_columns_default_correctly(db_session) -> None:
    tenant, tf, _lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    await db_session.refresh(tf)
    assert tf.last_agent_message_at is None
    assert tf.last_lead_message_at is None
    assert tf.follow_up_attempt_number == 0
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_follow_up_jobs_model.py -v`

Expected: FAIL — `ImportError: cannot import name 'FollowUpJob'`.

- [ ] **Step 3: Create the `FollowUpJob` model**

Create `src/ai_sdr/models/follow_up_job.py`:

```python
"""FollowUpJob — a scheduled or fired follow-up attempt for a lead.

Lifecycle: pending → completed | cancelled | error. Row stays forever
(audit). Pending rows are scanned every 60s by the follow_up_scanner
cron; once fired (or cancelled), they are terminal.

Schedule-one-at-a-time: each fired job inserts the next attempt's row
(unless max_attempts reached, which marks talkflow.status='cold').
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class FollowUpJob(Base):
    __tablename__ = "follow_up_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    talkflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("talkflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default="pending")
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Update `TalkFlow` model with 3 new columns**

Open `src/ai_sdr/models/talkflow.py`. Add the 3 columns inside the `TalkFlow` class (after `updated_at` or wherever fits stylistically):

```python
    last_agent_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_lead_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    follow_up_attempt_number: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="0"
    )
```

If `Integer` is not imported, add it: `from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func`.

- [ ] **Step 5: Re-export from models package**

Open `src/ai_sdr/models/__init__.py` and add:

```python
from ai_sdr.models.follow_up_job import FollowUpJob
```

…and add `"FollowUpJob"` to `__all__`. Keep alphabetical order.

- [ ] **Step 6: Verify import + run tests**

Run: `uv run python -c "from ai_sdr.models import FollowUpJob; print(FollowUpJob.__tablename__)"`

Expected: prints `follow_up_jobs`.

Controller validates the integration tests on VPS.

- [ ] **Step 7: Commit**

```bash
git add src/ai_sdr/models/follow_up_job.py src/ai_sdr/models/talkflow.py src/ai_sdr/models/__init__.py tests/integration/test_follow_up_jobs_model.py
git commit -m "$(cat <<'EOF'
feat(plan9 t3): FollowUpJob ORM + TalkFlow timing columns

3 new columns on talkflows (last_agent_message_at, last_lead_message_at,
follow_up_attempt_number) drive the timing + race-belt + counter logic
described in spec §6.1 + §6.3. FollowUpJob is the audit-friendly row
that persists every scheduled / fired / cancelled attempt.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Schema additions — `FollowUpStep`, `FollowUpConfig`, `ReengagementTemplate`

**Files:**
- Modify: `src/ai_sdr/schemas/treeflow_yaml.py`
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Create: `tests/unit/test_follow_up_config_schema.py`
- Create: `tests/unit/test_reengagement_template_schema.py`

**Design:** Two Pydantic schema additions. `FollowUpStep` validates ISO-8601 duration at parse-time (using `isodate` from T1). `FollowUpConfig` validates that `len(sequence) >= max_attempts` when `enabled=true`. `ReengagementTemplate` is a small leaf — just `template_ref + language + params`. The `follow_up: FollowUpConfig | None` field on `TreeFlow` and `reengagement_template: ReengagementTemplate | None` on `MessagingConfig` are both `Optional` so existing tenants work unchanged.

- [ ] **Step 1: Write failing tests for `FollowUpConfig`**

Create `tests/unit/test_follow_up_config_schema.py`:

```python
"""FollowUpConfig schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, FollowUpStep


def test_disabled_default() -> None:
    cfg = FollowUpConfig()
    assert cfg.enabled is False
    assert cfg.max_attempts == 3
    assert cfg.sequence == []


def test_enabled_with_full_sequence() -> None:
    cfg = FollowUpConfig.model_validate({
        "enabled": True,
        "max_attempts": 2,
        "sequence": [
            {"after": "PT24H", "template_ref": "followup_24h_v1"},
            {"after": "P3D", "template_ref": "followup_72h_v1"},
        ],
    })
    assert cfg.enabled is True
    assert len(cfg.sequence) == 2
    assert cfg.sequence[0].language == "pt_BR"  # default
    assert cfg.sequence[0].params == []


def test_enabled_requires_sequence_at_least_max_attempts() -> None:
    with pytest.raises(ValidationError, match="sequence has 1 entries"):
        FollowUpConfig.model_validate({
            "enabled": True,
            "max_attempts": 3,
            "sequence": [
                {"after": "PT24H", "template_ref": "x"},
            ],
        })


def test_disabled_allows_empty_sequence() -> None:
    cfg = FollowUpConfig.model_validate({
        "enabled": False,
        "max_attempts": 3,
        "sequence": [],
    })
    assert cfg.enabled is False


def test_max_attempts_bounds() -> None:
    with pytest.raises(ValidationError):
        FollowUpConfig.model_validate({"max_attempts": 0})
    with pytest.raises(ValidationError):
        FollowUpConfig.model_validate({"max_attempts": 11})


def test_after_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError, match="invalid ISO-8601 duration"):
        FollowUpStep.model_validate({"after": "24 hours", "template_ref": "x"})


def test_after_accepts_iso_8601_variants() -> None:
    for d in ("PT24H", "PT2H30M", "P1D", "P7D", "P1W"):
        s = FollowUpStep.model_validate({"after": d, "template_ref": "t"})
        assert s.after == d


def test_params_default_empty_list() -> None:
    s = FollowUpStep.model_validate({"after": "PT1H", "template_ref": "t"})
    assert s.params == []
```

- [ ] **Step 2: Implement the schemas**

Open `src/ai_sdr/schemas/treeflow_yaml.py`. Find the existing `TreeFlow` class. ABOVE it (or in a logical position with other config models), add:

```python
class FollowUpStep(BaseModel):
    """One attempt in a TreeFlow's follow-up sequence."""

    after: str                                  # ISO-8601 duration
    template_ref: str
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)

    @field_validator("after")
    @classmethod
    def _check_iso_duration(cls, v: str) -> str:
        from ai_sdr.follow_up.duration import parse_duration
        try:
            parse_duration(v)
        except Exception as e:
            raise ValueError(f"invalid ISO-8601 duration {v!r}: {e}") from e
        return v


class FollowUpConfig(BaseModel):
    """TreeFlow-level follow-up declaration.

    enabled + sequence (with template_refs pointing to Meta-registered
    HSM templates) + max_attempts. See spec §5 + §6.
    """

    enabled: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)
    sequence: list[FollowUpStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_sequence_length(self) -> "FollowUpConfig":
        if self.enabled and len(self.sequence) < self.max_attempts:
            raise ValueError(
                f"follow_up.sequence has {len(self.sequence)} entries but "
                f"max_attempts={self.max_attempts} — need at least max_attempts entries"
            )
        return self
```

Then find the `TreeFlow` class and add the field:

```python
    follow_up: FollowUpConfig | None = None
```

(Add imports as needed — `field_validator`, `model_validator`, `Field` from pydantic — they're likely already imported.)

- [ ] **Step 3: Add `ReengagementTemplate` to tenant_yaml.py**

Create `tests/unit/test_reengagement_template_schema.py`:

```python
"""ReengagementTemplate optional config under MessagingConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.schemas.tenant_yaml import MessagingConfig, ReengagementTemplate


def test_messaging_without_reengagement() -> None:
    cfg = MessagingConfig(provider="fake")
    assert cfg.reengagement_template is None


def test_reengagement_template_minimal() -> None:
    cfg = MessagingConfig.model_validate({
        "provider": "fake",
        "reengagement_template": {"template_ref": "reengagement_v1"},
    })
    assert cfg.reengagement_template is not None
    assert cfg.reengagement_template.template_ref == "reengagement_v1"
    assert cfg.reengagement_template.language == "pt_BR"
    assert cfg.reengagement_template.params == []


def test_reengagement_template_with_params() -> None:
    cfg = MessagingConfig.model_validate({
        "provider": "fake",
        "reengagement_template": {
            "template_ref": "reengagement_v1",
            "language": "pt_BR",
            "params": ["{{ collected.nome | default('amigo') }}"],
        },
    })
    assert cfg.reengagement_template.params == ["{{ collected.nome | default('amigo') }}"]


def test_reengagement_template_ref_required() -> None:
    with pytest.raises(ValidationError):
        MessagingConfig.model_validate({
            "provider": "fake",
            "reengagement_template": {"language": "pt_BR"},
        })
```

Open `src/ai_sdr/schemas/tenant_yaml.py`. ABOVE the `MessagingConfig` class, add:

```python
class ReengagementTemplate(BaseModel):
    """Tenant-level default template used by WindowExpiredError recovery.

    When worker's send_text raises WindowExpiredError (lead silent >24h),
    the worker falls back to send_template with this config. If tenant
    omits this block, recovery falls back to plain error logging."""

    template_ref: str
    language: str = "pt_BR"
    params: list[str] = Field(default_factory=list)
```

Then find `MessagingConfig` and add:

```python
    reengagement_template: ReengagementTemplate | None = None
```

- [ ] **Step 4: Run all schema tests**

Run: `uv run pytest tests/unit/test_follow_up_config_schema.py tests/unit/test_reengagement_template_schema.py -v`

Expected: all tests PASS. Note: `_check_iso_duration` depends on `ai_sdr.follow_up.duration.parse_duration` which lands in Task 5 — the import inside the validator is lazy, so test failures here mean Task 5 is needed first. If Task 5 doesn't exist yet, the import will raise at validation time. **Run Task 5 BEFORE Step 4 of this task** — OR temporarily move the `_check_iso_duration` validator out of `FollowUpStep`, then add it back when Task 5 lands.

**Recommended ordering**: do Task 5 first, then come back here. Update the task order tracker (this is a soft dependency, not a hard one).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/schemas/treeflow_yaml.py src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_follow_up_config_schema.py tests/unit/test_reengagement_template_schema.py
git commit -m "$(cat <<'EOF'
feat(plan9 t4): TreeFlow.follow_up + Tenant.messaging.reengagement_template schemas

FollowUpStep validates ISO-8601 duration at parse-time. FollowUpConfig
enforces sequence.len >= max_attempts when enabled=true.
ReengagementTemplate is optional — fallback to log-only when absent
(spec §1 Q5 fallback B).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `follow_up/duration.py` — ISO-8601 parser

**Files:**
- Create: `src/ai_sdr/follow_up/__init__.py` (empty)
- Create: `src/ai_sdr/follow_up/duration.py`
- Create: `tests/unit/test_follow_up_duration.py`

**Design:** Thin wrapper over `isodate`. The wrapper exists so call sites import a stable name; we can swap parsers later (e.g., add support for `"24h"` shorthand) without touching consumers.

- [ ] **Step 1: Create the package skeleton**

Run: `mkdir -p src/ai_sdr/follow_up && touch src/ai_sdr/follow_up/__init__.py`

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_follow_up_duration.py`:

```python
"""parse_duration — ISO-8601 → timedelta."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ai_sdr.follow_up.duration import parse_duration


def test_pt24h() -> None:
    assert parse_duration("PT24H") == timedelta(hours=24)


def test_pt2h30m() -> None:
    assert parse_duration("PT2H30M") == timedelta(hours=2, minutes=30)


def test_p1d() -> None:
    assert parse_duration("P1D") == timedelta(days=1)


def test_p7d() -> None:
    assert parse_duration("P7D") == timedelta(days=7)


def test_p1w() -> None:
    assert parse_duration("P1W") == timedelta(weeks=1)


def test_invalid_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_duration("24 hours")


def test_empty_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        parse_duration("")


def test_pt0s_is_zero_delta() -> None:
    assert parse_duration("PT0S") == timedelta(0)
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_follow_up_duration.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

Create `src/ai_sdr/follow_up/duration.py`:

```python
"""ISO-8601 duration → timedelta. Thin wrapper over `isodate`."""

from __future__ import annotations

from datetime import timedelta

import isodate


def parse_duration(s: str) -> timedelta:
    """Parse an ISO-8601 duration string into a timedelta.

    Examples:
      "PT24H" → 24h
      "P1D"   → 1 day
      "P1W"   → 7 days
      "PT2H30M" → 2h30m

    Raises ValueError on invalid input (empty string, non-ISO format).
    """
    if not s:
        raise ValueError("empty duration string")
    try:
        result = isodate.parse_duration(s)
    except (isodate.ISO8601Error, Exception) as e:
        raise ValueError(f"invalid ISO-8601 duration {s!r}: {e}") from e
    if isinstance(result, timedelta):
        return result
    # isodate may return Duration (its own class) for month-anchored durations
    # like "P1M". For our follow-up use case we don't allow month-relative;
    # surface as ValueError.
    raise ValueError(
        f"month/year-relative durations not supported (got {s!r}); "
        f"use weeks (P*W) or days (P*D)"
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_follow_up_duration.py -v`

Expected: all 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/follow_up/__init__.py src/ai_sdr/follow_up/duration.py tests/unit/test_follow_up_duration.py
git commit -m "$(cat <<'EOF'
feat(plan9 t5): follow_up.duration.parse_duration — ISO-8601 → timedelta

Thin wrapper over isodate. Rejects month-anchored durations (P1M, P1Y)
because they're not well-defined timedeltas without an anchor date —
follow-up sequences use weeks/days/hours only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `follow_up/jinja.py` — sandboxed param render

**Files:**
- Create: `src/ai_sdr/follow_up/jinja.py`
- Create: `tests/unit/test_follow_up_jinja.py`

**Design:** `render_params(params, lead, talkflow, tenant) → list[str]`. Each entry in `params` is a Jinja string; render each independently. Uses `jinja2.sandbox.SandboxedEnvironment` to block dunder/attribute escapes. Supports filters: `default`, `lower`, `upper`, `trim`, `truncate`.

`collected` comes from the TalkFlow's checkpointer state (extracted fields). For render time, we pass the current state's `collected` dict if available, else `{}`. The talkflow ORM doesn't carry `collected` directly — we read it from the LangGraph checkpoint via the runtime. To avoid coupling render to runtime, this task takes `collected: dict` as a parameter; the caller (scanner job, inbound recovery) loads it from the checkpointer.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_follow_up_jinja.py`:

```python
"""Sandboxed Jinja2 param rendering for HSM templates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ai_sdr.follow_up.jinja import render_params


def _lead(whatsapp_e164="+5511999", external_label=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        whatsapp_e164=whatsapp_e164,
        external_label=external_label,
    )


def _tenant(slug="joana"):
    return SimpleNamespace(slug=slug, display_name=slug.title())


def test_renders_collected_field() -> None:
    params = ["{{ collected.nome }}"]
    out = render_params(params, lead=_lead(), tenant=_tenant(), collected={"nome": "Maria"})
    assert out == ["Maria"]


def test_default_filter_when_missing() -> None:
    params = ["{{ collected.nome | default('amigo') }}"]
    out = render_params(params, lead=_lead(), tenant=_tenant(), collected={})
    assert out == ["amigo"]


def test_lead_field() -> None:
    params = ["{{ lead.whatsapp_e164 }}"]
    out = render_params(params, lead=_lead("+5511999"), tenant=_tenant(), collected={})
    assert out == ["+5511999"]


def test_tenant_field() -> None:
    params = ["{{ tenant.display_name }}"]
    out = render_params(params, lead=_lead(), tenant=_tenant("joana"), collected={})
    assert out == ["Joana"]


def test_multiple_params_independent() -> None:
    params = ["{{ collected.nome }}", "{{ tenant.slug }}"]
    out = render_params(params, lead=_lead(), tenant=_tenant("acme"), collected={"nome": "X"})
    assert out == ["X", "acme"]


def test_sandbox_blocks_dunder_access() -> None:
    params = ["{{ collected.__class__.__mro__ }}"]
    with pytest.raises(Exception):  # jinja2.exceptions.SecurityError
        render_params(params, lead=_lead(), tenant=_tenant(), collected={})


def test_sandbox_blocks_import() -> None:
    params = ["{{ ''.__class__.__bases__[0].__subclasses__() }}"]
    with pytest.raises(Exception):
        render_params(params, lead=_lead(), tenant=_tenant(), collected={})


def test_truncate_filter() -> None:
    params = ["{{ collected.bio | truncate(10) }}"]
    out = render_params(params, lead=_lead(), tenant=_tenant(), collected={"bio": "x" * 50})
    # Default truncate adds an ellipsis (length includes it).
    assert len(out[0]) <= 13


def test_empty_params_list() -> None:
    assert render_params([], lead=_lead(), tenant=_tenant(), collected={}) == []
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_follow_up_jinja.py -v`

Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Create `src/ai_sdr/follow_up/jinja.py`:

```python
"""Render HSM template parameters via sandboxed Jinja2.

Each entry in `params` is a small Jinja string (typically `{{ ... }}`).
We render them independently against a context that exposes:
  - `collected` — TalkFlow's extracted fields dict (from LangGraph state)
  - `lead`       — Lead ORM row (whatsapp_e164, external_label)
  - `tenant`     — Tenant ORM row (slug, display_name)

The sandbox blocks dunder access and arbitrary attribute traversal,
so a malicious template author can't pivot to module imports.
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


_env = SandboxedEnvironment(
    autoescape=False,            # HSM params are not HTML; plaintext
    undefined=StrictUndefined,   # missing var raises (caller must use `| default(...)`)
)


def render_params(
    params: list[str],
    *,
    lead: Any,
    tenant: Any,
    collected: dict[str, Any],
) -> list[str]:
    """Render each param string in the context. Returns the resulting list
    (same length as `params`)."""
    out: list[str] = []
    for p in params:
        template = _env.from_string(p)
        out.append(template.render(collected=collected, lead=lead, tenant=tenant))
    return out
```

Note on `StrictUndefined`: missing variables raise instead of rendering as empty string. This forces template authors to use `| default('...')` explicitly, which avoids "Oi , tudo bem?" with awkward spacing.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_follow_up_jinja.py -v`

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/follow_up/jinja.py tests/unit/test_follow_up_jinja.py
git commit -m "$(cat <<'EOF'
feat(plan9 t6): follow_up.jinja.render_params — sandboxed Jinja2

SandboxedEnvironment blocks dunder/import escapes. StrictUndefined
forces template authors to use `| default('...')` for optional vars.
Filters available: default, lower, upper, trim, truncate (all built-in).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `follow_up/treeflow_loader.py` — load follow-up config from TalkFlow

**Files:**
- Create: `src/ai_sdr/follow_up/treeflow_loader.py`
- Create: `tests/integration/test_follow_up_treeflow_loader.py`

**Design:** Given a `TalkFlow`, returns the `FollowUpConfig | None` from the underlying TreeflowVersion's YAML. Reads `talkflow.treeflow_version_id → TreeflowVersion.content_yaml`, parses to `TreeFlow` schema, returns `treeflow.follow_up`.

The function is async (DB lookup of TreeflowVersion) and is called from the scanner job + worker inbound path. Keep it pure (no side effects) so callers can compose freely.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_follow_up_treeflow_loader.py`:

```python
"""load_treeflow_follow_up — reads TreeFlow.follow_up from TalkFlow's pinned version."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


_YAML_WITH_FOLLOWUP = """
id: t1
version: 1.0.0
entry_node: n1
nodes:
  n1:
    prompt: hi
follow_up:
  enabled: true
  max_attempts: 2
  sequence:
    - after: PT24H
      template_ref: followup_24h_v1
      language: pt_BR
      params: ["{{ collected.nome | default('amigo') }}"]
    - after: P3D
      template_ref: followup_72h_v1
"""

_YAML_NO_FOLLOWUP = """
id: t1
version: 1.0.0
entry_node: n1
nodes:
  n1:
    prompt: hi
"""


async def _make_talkflow(db_session, yaml_str: str) -> TalkFlow:
    tenant = Tenant(slug=f"l_{uuid.uuid4().hex[:6]}", display_name="L")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml=yaml_str,
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tf


async def test_returns_config_when_present(db_session) -> None:
    tf = await _make_talkflow(db_session, _YAML_WITH_FOLLOWUP)
    cfg = await load_treeflow_follow_up(db_session, tf)
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.max_attempts == 2
    assert len(cfg.sequence) == 2
    assert cfg.sequence[0].template_ref == "followup_24h_v1"


async def test_returns_none_when_absent(db_session) -> None:
    tf = await _make_talkflow(db_session, _YAML_NO_FOLLOWUP)
    cfg = await load_treeflow_follow_up(db_session, tf)
    assert cfg is None
```

- [ ] **Step 2: Implement**

Create `src/ai_sdr/follow_up/treeflow_loader.py`:

```python
"""Load the FollowUpConfig from a TalkFlow's pinned TreeflowVersion.

Returns None when the TreeFlow has no `follow_up:` block. Returns a
parsed FollowUpConfig otherwise. The caller is responsible for
checking `cfg.enabled` before scheduling — this loader is intentionally
liberal (returns even disabled configs) so debug/dry-run can inspect
what's declared without acting on it.
"""

from __future__ import annotations

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, TreeFlow


async def load_treeflow_follow_up(
    session: AsyncSession,
    talkflow: TalkFlow,
) -> FollowUpConfig | None:
    """Return the parsed FollowUpConfig from the TreeFlow YAML pinned to
    this TalkFlow, or None if no `follow_up:` block exists."""
    tv = await session.get(TreeflowVersion, talkflow.treeflow_version_id)
    if tv is None:
        return None
    parsed = TreeFlow.model_validate(yaml.safe_load(tv.content_yaml))
    return parsed.follow_up
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/integration/test_follow_up_treeflow_loader.py -v` (VPS validates).

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/follow_up/treeflow_loader.py tests/integration/test_follow_up_treeflow_loader.py
git commit -m "$(cat <<'EOF'
feat(plan9 t7): load_treeflow_follow_up — parse follow_up from TreeflowVersion

Reads talkflow.treeflow_version_id → TreeflowVersion.content_yaml,
parses to TreeFlow schema, returns follow_up. Returns None when absent
(liberal — dry-run/debug can inspect disabled configs too).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `MessagingAdapter.send_template` ABC + `FakeMessagingAdapter` impl

**Files:**
- Modify: `src/ai_sdr/messaging/base.py`
- Modify: `src/ai_sdr/messaging/fake.py`
- Create: `tests/unit/test_messaging_base_send_template.py`
- Create: `tests/unit/test_fake_send_template.py`

**Design:** Add abstract `send_template` to `MessagingAdapter`. Test that subclasses without an impl can't instantiate (ABC enforcement). Then add `FakeMessagingAdapter.send_template` that records calls to `sent_templates: list[tuple[to, ref, lang, params]]` and supports `fail_next_template_send(exc)` scripting (parallel to `fail_next_send` from P5).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_messaging_base_send_template.py`:

```python
"""ABC enforcement — subclass without send_template can't instantiate."""

from __future__ import annotations

from typing import Mapping

import pytest

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult


def test_subclass_without_send_template_fails() -> None:
    class _Incomplete(MessagingAdapter):
        async def handle_inbound(self, raw_body: bytes, headers: Mapping[str, str]):
            return []

        async def send_text(self, to: str, text: str) -> SendResult:
            return SendResult(external_id="x", sent_at_iso="now")

        def verification_challenge(self, params: Mapping[str, str]) -> str | None:
            return None

    with pytest.raises(TypeError, match="abstract"):
        _Incomplete()


def test_complete_subclass_instantiates() -> None:
    class _Complete(MessagingAdapter):
        async def handle_inbound(self, raw_body, headers):
            return []

        async def send_text(self, to, text):
            return SendResult(external_id="x", sent_at_iso="now")

        async def send_template(self, to, template_ref, language, params):
            return SendResult(external_id="t", sent_at_iso="now")

        def verification_challenge(self, params):
            return None

    a = _Complete()
    assert isinstance(a, MessagingAdapter)
```

Create `tests/unit/test_fake_send_template.py`:

```python
"""FakeMessagingAdapter.send_template behavioral tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter


async def test_send_template_records_call() -> None:
    fake = FakeMessagingAdapter()
    r = await fake.send_template(
        to="+5511999",
        template_ref="followup_24h_v1",
        language="pt_BR",
        params=["Maria"],
    )
    assert fake.sent_templates == [
        ("+5511999", "followup_24h_v1", "pt_BR", ["Maria"]),
    ]
    assert r.external_id


async def test_fail_next_template_send_raises_once() -> None:
    fake = FakeMessagingAdapter()
    fake.fail_next_template_send(RecipientUnreachable("not on WA"))

    with pytest.raises(RecipientUnreachable):
        await fake.send_template("+5511999", "x", "pt_BR", [])

    # Next call succeeds
    r = await fake.send_template("+5511999", "x", "pt_BR", [])
    assert r.external_id


async def test_send_text_and_send_template_independent_buffers() -> None:
    fake = FakeMessagingAdapter()
    await fake.send_text("+1", "hi")
    await fake.send_template("+2", "ref", "pt_BR", ["X"])
    assert fake.sent_messages == [("+1", "hi")]
    assert fake.sent_templates == [("+2", "ref", "pt_BR", ["X"])]
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_base_send_template.py tests/unit/test_fake_send_template.py -v`

Expected: FAIL — `send_template` doesn't exist on base or fake.

- [ ] **Step 3: Add `send_template` to base ABC**

Open `src/ai_sdr/messaging/base.py`. Add to the `MessagingAdapter` class (after `send_text`):

```python
    @abstractmethod
    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        """Send a pre-approved HSM template. Provider validates template_ref
        + language + params shape against its registered templates.

        Returns SendResult (same shape as send_text). Adapter retries
        Transient/RateLimit internally; raises typed terminal errors
        (AuthError, RecipientUnreachable, PolicyError) on terminal failures.

        WindowExpiredError should NEVER fire for templates — HSM messages
        bypass the 24h window. If it does, treat as adapter bug.
        """
```

- [ ] **Step 4: Implement on `FakeMessagingAdapter`**

Open `src/ai_sdr/messaging/fake.py`. Modify the class:

1. Add field in `__init__`:
```python
        self.sent_templates: list[tuple[str, str, str, list[str]]] = []
        self._pending_template_failure: TerminalError | None = None
```

2. Add scripting hook (near `fail_next_send`):
```python
    def fail_next_template_send(self, exc: TerminalError) -> None:
        """Force next send_template to raise this exception once."""
        self._pending_template_failure = exc
```

3. Add the new method (near `send_text`):
```python
    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        if self._pending_template_failure is not None:
            exc = self._pending_template_failure
            self._pending_template_failure = None
            raise exc
        self.sent_templates.append((to, template_ref, language, list(params)))
        return SendResult(
            external_id=f"faketmpl_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(timezone.utc).isoformat(),
        )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_messaging_base_send_template.py tests/unit/test_fake_send_template.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/base.py src/ai_sdr/messaging/fake.py tests/unit/test_messaging_base_send_template.py tests/unit/test_fake_send_template.py
git commit -m "$(cat <<'EOF'
feat(plan9 t8): MessagingAdapter.send_template abstract + FakeMessagingAdapter impl

Additive contract change. FakeMessagingAdapter records calls to a
parallel `sent_templates` list and has `fail_next_template_send`
scripting hook. WhatsAppCloudAPIAdapter impl lands in next task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: `WhatsAppCloudAPIAdapter.send_template` (HSM payload)

**Files:**
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Create: `tests/unit/test_whatsapp_send_template_payload.py`

**Design:** Implements `send_template` using Meta's `POST /messages type=template` endpoint. Same retry stack (`tenacity.AsyncRetrying`, `_WAIT_STRATEGY`), same `_classify_error` taxonomy, same `_build_http_client` factory (so tests can mock).

Meta HSM payload:
```json
{
  "messaging_product": "whatsapp",
  "to": "<E.164 no +>",
  "type": "template",
  "template": {
    "name": "<template_ref>",
    "language": {"code": "<language>"},
    "components": [
      {
        "type": "body",
        "parameters": [
          {"type": "text", "text": "<param 1>"},
          {"type": "text", "text": "<param 2>"}
        ]
      }
    ]
  }
}
```

If `params` is empty, omit the `components` array (no body params in template).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_whatsapp_send_template_payload.py`:

```python
"""WhatsAppCloudAPIAdapter.send_template — payload shape + error classification."""

from __future__ import annotations

import httpx
import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RecipientUnreachable,
    TransientError,
)
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@pytest.fixture
def adapter_no_retry_sleep(monkeypatch) -> WhatsAppCloudAPIAdapter:
    import tenacity

    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "PNID", "wa_token": "TOKEN",
        "wa_verify": "vt", "wa_app_secret": "as",
    }
    a = WhatsAppCloudAPIAdapter(cfg, secrets)
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY", tenacity.wait_none()
    )
    return a


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+5511999", "wa_id": "5511999"}],
            "messages": [{"id": "wamid.TPL_OUT="}],
        },
    )


def _error_response(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "err"}})


async def test_payload_shape_with_params(adapter_no_retry_sleep, monkeypatch) -> None:
    captured = {}

    def transport(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content)
        return _ok_response()

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_template(
        to="+5511999",
        template_ref="followup_24h_v1",
        language="pt_BR",
        params=["Maria", "mentoria"],
    )
    assert r.external_id == "wamid.TPL_OUT="
    # URL contains phone_number_id + /messages
    assert "/PNID/messages" in captured["url"]
    body = captured["body"]
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "5511999"  # no + prefix per Meta API
    assert body["type"] == "template"
    assert body["template"]["name"] == "followup_24h_v1"
    assert body["template"]["language"]["code"] == "pt_BR"
    assert body["template"]["components"][0]["type"] == "body"
    assert body["template"]["components"][0]["parameters"] == [
        {"type": "text", "text": "Maria"},
        {"type": "text", "text": "mentoria"},
    ]


async def test_payload_shape_without_params(adapter_no_retry_sleep, monkeypatch) -> None:
    captured = {}

    def transport(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content)
        return _ok_response()

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    await adapter_no_retry_sleep.send_template(
        to="+5511999", template_ref="x", language="pt_BR", params=[],
    )
    # When params is empty, components MUST be omitted (Meta API rejects empty components)
    assert "components" not in captured["body"]["template"]


@pytest.mark.parametrize(
    "status, code, expected_exc",
    [
        (401, 190, AuthError),
        (400, 131026, RecipientUnreachable),
        (400, 131049, PolicyError),
        (503, 2, TransientError),
    ],
)
async def test_classifies_errors_same_as_send_text(
    adapter_no_retry_sleep, monkeypatch, status, code, expected_exc
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: _error_response(status, code)),
            timeout=15.0,
        ),
    )
    with pytest.raises(expected_exc):
        await adapter_no_retry_sleep.send_template(
            "+5511999", "x", "pt_BR", [],
        )
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_whatsapp_send_template_payload.py -v`

Expected: FAIL — `send_template` not implemented.

- [ ] **Step 3: Implement on `WhatsAppCloudAPIAdapter`**

Open `src/ai_sdr/messaging/whatsapp_cloud.py`. Add the method inside the class (near `send_text`):

```python
    async def send_template(
        self,
        to: str,
        template_ref: str,
        language: str,
        params: list[str],
    ) -> SendResult:
        url = (
            f"https://graph.facebook.com/{self._api_version}/"
            f"{self._phone_number_id}/messages"
        )
        template_block: dict[str, object] = {
            "name": template_ref,
            "language": {"code": language},
        }
        if params:
            template_block["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params],
                }
            ]
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": template_block,
        }
        request_headers = {"Authorization": f"Bearer {self._access_token}"}

        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT_STRATEGY,
            retry=tenacity.retry_if_exception_type(TransientError),
            reraise=True,
        )

        log.info("wa.send_template.start", to=to, template_ref=template_ref)
        async for attempt in retryer:
            with attempt:
                async with _build_http_client() as client:
                    response = await client.post(url, json=body, headers=request_headers)
                if response.status_code == 200:
                    data = response.json()
                    out_id = data["messages"][0]["id"]
                    log.info(
                        "wa.send_template.success",
                        to=to,
                        template_ref=template_ref,
                        external_id=out_id,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    return SendResult(
                        external_id=out_id,
                        sent_at_iso=datetime.now(timezone.utc).isoformat(),
                    )
                try:
                    err_body = response.json().get("error")
                except Exception:
                    err_body = None
                retry_after_hdr = response.headers.get("Retry-After")
                retry_after_s = int(retry_after_hdr) if retry_after_hdr else None
                exc = _classify_error(response.status_code, err_body, retry_after_s)
                log.warning(
                    "wa.send_template.error",
                    to=to,
                    template_ref=template_ref,
                    status=response.status_code,
                    err_type=type(exc).__name__,
                    err=str(exc),
                )
                raise exc
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_whatsapp_send_template_payload.py -v`

Expected: all tests PASS (4 parametrized + 2 standalone = 6).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/whatsapp_cloud.py tests/unit/test_whatsapp_send_template_payload.py
git commit -m "$(cat <<'EOF'
feat(plan9 t9): WhatsAppCloudAPIAdapter.send_template — HSM via Meta API

POST /messages type=template with template.name + language + body params.
Reuses _WAIT_STRATEGY (tenacity), _build_http_client (httpx mock-friendly),
and _classify_error (same Meta error code taxonomy as send_text).

When params is empty, omits the components block — Meta API rejects
empty components arrays.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `follow_up/scheduler.py` — shared helpers

**Files:**
- Create: `src/ai_sdr/follow_up/scheduler.py`
- Create: `tests/integration/test_follow_up_scheduler_helpers.py`

**Design:** Three pure helpers shared by `process_lead_inbox` and `_fire_follow_up`. Each function does ONE thing.

- `cancel_pending_for_lead(db, lead_id, reason)` — bulk UPDATE all pending jobs for the lead.
- `schedule_next_followup(db, talkflow, lead, tenant, follow_up_config, next_attempt_number)` — INSERT one job at `now() + sequence[next-1].after`.
- `mark_cold_if_exhausted(talkflow, follow_up_config, attempt_number) -> bool` — pure (no DB), returns True if `attempt_number >= max_attempts` and sets `talkflow.status='cold'`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_follow_up_scheduler_helpers.py`:

```python
"""follow_up.scheduler — cancel_pending_for_lead, schedule_next_followup, mark_cold_if_exhausted."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.scheduler import (
    cancel_pending_for_lead,
    mark_cold_if_exhausted,
    schedule_next_followup,
)
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import FollowUpConfig, FollowUpStep

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"s_{uuid.uuid4().hex[:6]}", display_name="S")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tenant, tf, lead


def _config() -> FollowUpConfig:
    return FollowUpConfig(
        enabled=True,
        max_attempts=3,
        sequence=[
            FollowUpStep(after="PT24H", template_ref="t1"),
            FollowUpStep(after="P3D", template_ref="t2"),
            FollowUpStep(after="P7D", template_ref="t3"),
        ],
    )


async def test_cancel_pending_for_lead_marks_only_pending(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)

    # 1 pending + 1 completed + 1 cancelled
    db_session.add_all([
        FollowUpJob(tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
                    attempt_number=1, scheduled_at=datetime.now(UTC), status="pending"),
        FollowUpJob(tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
                    attempt_number=2, scheduled_at=datetime.now(UTC), status="completed",
                    fired_at=datetime.now(UTC)),
        FollowUpJob(tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
                    attempt_number=3, scheduled_at=datetime.now(UTC), status="cancelled"),
    ])
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    rowcount = await cancel_pending_for_lead(db_session, lead.id, reason="lead responded")
    await db_session.commit()
    assert rowcount == 1

    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(
        select(FollowUpJob).where(FollowUpJob.lead_id == lead.id).order_by(FollowUpJob.attempt_number)
    )).scalars().all()
    assert [r.status for r in rows] == ["cancelled", "completed", "cancelled"]
    assert rows[0].error_detail == "lead responded"


async def test_schedule_next_followup_inserts_with_correct_delay(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    cfg = _config()
    before = datetime.now(UTC)
    await schedule_next_followup(db_session, tf, lead, tenant, cfg, next_attempt_number=2)
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    job = (await db_session.execute(
        select(FollowUpJob).where(FollowUpJob.lead_id == lead.id)
    )).scalar_one()
    assert job.attempt_number == 2
    assert job.status == "pending"
    # sequence[1] is "P3D" → 72h
    expected_delta = timedelta(days=3)
    assert before + expected_delta - timedelta(seconds=5) <= job.scheduled_at <= datetime.now(UTC) + expected_delta + timedelta(seconds=5)


def test_mark_cold_if_exhausted() -> None:
    tf = TalkFlow(
        tenant_id=uuid.uuid4(), lead_id=uuid.uuid4(),
        treeflow_version_id=uuid.uuid4(), thread_id="x",
    )
    tf.status = "active"
    cfg = _config()  # max_attempts=3

    # attempt 1 → not exhausted
    assert mark_cold_if_exhausted(tf, cfg, 1) is False
    assert tf.status == "active"

    # attempt 3 → exhausted
    assert mark_cold_if_exhausted(tf, cfg, 3) is True
    assert tf.status == "cold"
```

- [ ] **Step 2: Implement**

Create `src/ai_sdr/follow_up/scheduler.py`:

```python
"""Shared scheduler helpers — used by process_lead_inbox and _fire_follow_up."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.follow_up.duration import parse_duration
from ai_sdr.models.follow_up_job import FollowUpJob

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant
    from ai_sdr.schemas.treeflow_yaml import FollowUpConfig


async def cancel_pending_for_lead(
    session: AsyncSession,
    lead_id: uuid.UUID,
    *,
    reason: str,
) -> int:
    """Mark all pending follow_up_jobs for this lead as cancelled.

    Returns the number of rows affected. Caller commits."""
    result = await session.execute(
        update(FollowUpJob)
        .where(FollowUpJob.lead_id == lead_id, FollowUpJob.status == "pending")
        .values(status="cancelled", error_detail=reason)
    )
    return result.rowcount or 0


async def schedule_next_followup(
    session: AsyncSession,
    talkflow: "TalkFlow",
    lead: "Lead",
    tenant: "Tenant",
    follow_up_config: "FollowUpConfig",
    *,
    next_attempt_number: int,
) -> FollowUpJob:
    """Insert one follow_up_jobs row at now() + sequence[next-1].after.

    next_attempt_number is 1-based (1 = first follow-up, 2 = second, ...).
    Caller commits."""
    step = follow_up_config.sequence[next_attempt_number - 1]
    delta = parse_duration(step.after)
    scheduled_at = datetime.now(UTC) + delta
    job = FollowUpJob(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        attempt_number=next_attempt_number,
        scheduled_at=scheduled_at,
        status="pending",
    )
    session.add(job)
    await session.flush()
    return job


def mark_cold_if_exhausted(
    talkflow: "TalkFlow",
    follow_up_config: "FollowUpConfig",
    last_attempt_number: int,
) -> bool:
    """Pure helper. If last_attempt_number >= max_attempts, sets
    talkflow.status = 'cold' and returns True. Otherwise no-op + False."""
    if last_attempt_number >= follow_up_config.max_attempts:
        talkflow.status = "cold"
        return True
    return False
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/integration/test_follow_up_scheduler_helpers.py -v` (VPS).

Expected: 3 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/follow_up/scheduler.py tests/integration/test_follow_up_scheduler_helpers.py
git commit -m "$(cat <<'EOF'
feat(plan9 t10): follow_up.scheduler helpers — cancel/schedule/mark_cold

Three pure-ish helpers shared by inbound worker and scanner job:
- cancel_pending_for_lead: bulk UPDATE pending → cancelled
- schedule_next_followup: insert one row at now() + sequence[N].after
- mark_cold_if_exhausted: pure (no DB), flips talkflow.status

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `process_lead_inbox` — cancel on inbound + cold-reactivate + schedule-after-send

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py`
- Create: `tests/integration/test_follow_up_cancellation_on_inbound.py`

**Design:** Wire three new behaviors into the existing P5 worker job:

1. **On every inbound arrival** (before `runtime.step`): cancel pending follow-ups, reset attempt counter, reactivate cold talkflow, update `last_lead_message_at`.
2. **After `send_text` success**: update `last_agent_message_at`, schedule attempt 1.

(WindowExpiredError recovery is the next task — keep it separate so this commit stays focused.)

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_follow_up_cancellation_on_inbound.py`:

```python
"""On inbound: cancel pending follow-ups + reset counter + cold→active + schedule attempt 1."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration


_YAML_FOLLOWUP = """
id: t1
version: 1.0.0
entry_node: n1
nodes: {n1: {prompt: hi}}
follow_up:
  enabled: true
  max_attempts: 2
  sequence:
    - after: PT1H
      template_ref: t1
    - after: PT2H
      template_ref: t2
"""


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def _seed_inactive_lead(db_session, *, talkflow_status="active") -> tuple:
    tenant = Tenant(slug=f"inb_{uuid.uuid4().hex[:6]}", display_name="I")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64, content_yaml=_YAML_FOLLOWUP,
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_agent_message_at=datetime.now(UTC) - timedelta(hours=10),
        follow_up_attempt_number=2,
    )
    tf.status = talkflow_status
    db_session.add(tf)
    await db_session.flush()

    # Pre-existing pending follow-up + a queued inbound (this triggers the worker)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=3, scheduled_at=datetime.now(UTC) + timedelta(hours=10),
        status="pending",
    ))
    db_session.add(InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="estou de volta",
        received_at=datetime.now(UTC), raw={},
    ))
    await db_session.commit()
    return tenant, tf, lead


def _ctx(session_factory, adapter, runtime_response_text="oi"):
    async def runtime_step_stub(*args, **kwargs):
        return MagicMock(response_text=runtime_response_text)
    runtime = MagicMock()
    runtime.step = runtime_step_stub
    registry = MagicMock()
    registry.get.return_value = adapter
    return {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime}


async def test_inbound_cancels_pending_and_resets_counter(
    db_session, session_factory
) -> None:
    tenant, tf, lead = await _seed_inactive_lead(db_session)
    adapter = FakeMessagingAdapter()

    await process_lead_inbox(
        _ctx(session_factory, adapter),
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(tf)
    assert tf.follow_up_attempt_number == 0
    assert tf.last_lead_message_at is not None

    # Pre-existing pending → cancelled
    jobs = (await db_session.execute(
        select(FollowUpJob).where(FollowUpJob.lead_id == lead.id).order_by(FollowUpJob.created_at.asc())
    )).scalars().all()
    pre_existing = jobs[0]
    assert pre_existing.status == "cancelled"
    assert pre_existing.error_detail == "lead responded"

    # New attempt 1 scheduled (in-flight TreeFlow has follow_up.enabled=true)
    assert len(jobs) >= 2
    new_jobs = [j for j in jobs if j.status == "pending"]
    assert len(new_jobs) == 1
    assert new_jobs[0].attempt_number == 1


async def test_inbound_reactivates_cold_talkflow(
    db_session, session_factory
) -> None:
    tenant, tf, lead = await _seed_inactive_lead(db_session, talkflow_status="cold")
    adapter = FakeMessagingAdapter()

    await process_lead_inbox(
        _ctx(session_factory, adapter),
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(tf)
    assert tf.status == "active"
```

- [ ] **Step 2: Modify `process_lead_inbox`**

Open `src/ai_sdr/worker/jobs/inbound.py`. Add imports at the top:

```python
from ai_sdr.follow_up.scheduler import (
    cancel_pending_for_lead,
    schedule_next_followup,
)
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
```

Find the section that loads `talkflow` and starts the drain loop. **Just before the drain loop** (after lead.status check + talkflow load), insert the cancellation + reactivation block:

```python
            # P9: lead responded — cancel pending follow-ups, reset counter,
            # reactivate cold talkflow.
            cancelled = await cancel_pending_for_lead(db, lead.id, reason="lead responded")
            if cancelled:
                log.info("follow_up.cancelled_on_inbound", lead_id=str(lead.id), n=cancelled)
            talkflow.follow_up_attempt_number = 0
            if talkflow.status == "cold":
                talkflow.status = "active"
                log.info("follow_up.cold_reactivated", talkflow_id=str(talkflow.id))
```

Then INSIDE the drain loop, find the `_process_one` call (or wherever the success path of `adapter.send_text` updates `msg.status='processed'`). After that update, add:

```python
                    # P9: agent just spoke — update timestamps + schedule next follow-up.
                    talkflow.last_agent_message_at = datetime.now(UTC)
                    talkflow.last_lead_message_at = msg.received_at
                    tf_config = await load_treeflow_follow_up(db, talkflow)
                    if tf_config and tf_config.enabled and tf_config.sequence:
                        await schedule_next_followup(
                            db, talkflow, lead, tenant, tf_config,
                            next_attempt_number=1,
                        )
                        log.info(
                            "follow_up.first_scheduled",
                            lead_id=str(lead.id),
                            at=(datetime.now(UTC) + parse_duration(tf_config.sequence[0].after)).isoformat(),
                        )
```

(`datetime.now(UTC)` + `parse_duration` are already imported via tasks 5 + earlier inbound.py imports — add to imports if needed.)

The exact line within `_process_one` depends on the current file shape after Plan 5 + P5 fixes. Look for `msg.status = "processed"` and `msg.processed_at = datetime.now(UTC)` — insert the new block right after, before `await db.commit()`.

- [ ] **Step 3: Run tests**

Run on VPS: `uv run pytest tests/integration/test_follow_up_cancellation_on_inbound.py -v`

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_follow_up_cancellation_on_inbound.py
git commit -m "$(cat <<'EOF'
feat(plan9 t11): process_lead_inbox cancels pending + schedules on send

Three new behaviors in the inbound worker:
1. On inbound arrival: cancel_pending_for_lead, reset attempt_number=0,
   cold→active.
2. After successful send_text: update last_agent_message_at,
   last_lead_message_at, and schedule attempt 1 if follow_up enabled.

Spec §6.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `process_lead_inbox` — WindowExpiredError recovery via reengagement template

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py`
- Create: `tests/integration/test_window_expired_recovery.py`
- Create: `tests/integration/test_window_expired_no_template_fallback.py`

**Design:** In the `except WindowExpiredError` branch of `_process_one`, instead of just marking `msg.status='error'` and returning, attempt to fall back to `adapter.send_template(reengagement_template)` from tenant config. If the template config is missing → log warning + mark error (existing P5 behavior).

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_window_expired_recovery.py`:

```python
"""WindowExpiredError on send_text triggers reengagement template fallback."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import WindowExpiredError
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration


def _tenant_yaml_with_reengagement(slug: str) -> str:
    return f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: anthropic_key
messaging:
  provider: fake
  reengagement_template:
    template_ref: reengagement_v1
    language: pt_BR
    params: ["{{{{ collected.nome | default('amigo') }}}}"]
"""


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


def _patch_tenants_dir(monkeypatch, td):
    from ai_sdr.settings import get_settings
    monkeypatch.setattr(get_settings(), "tenants_dir", str(td))


async def _seed(db_session, isolated_tenants_dir):
    tenant = Tenant(slug=f"wer_{uuid.uuid4().hex[:6]}", display_name="WER")
    db_session.add(tenant)
    await db_session.flush()

    (isolated_tenants_dir / tenant.slug).mkdir()
    (isolated_tenants_dir / tenant.slug / "tenant.yaml").write_text(
        _tenant_yaml_with_reengagement(tenant.slug)
    )

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.flush()

    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound)
    await db_session.commit()
    return tenant, tf, lead, inbound


async def test_window_expired_recovers_via_template(
    db_session, isolated_tenants_dir, session_factory, monkeypatch
) -> None:
    _patch_tenants_dir(monkeypatch, isolated_tenants_dir)
    tenant, tf, lead, inbound = await _seed(db_session, isolated_tenants_dir)

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(WindowExpiredError("24h expired"))

    async def runtime_step_stub(*args, **kwargs):
        return MagicMock(response_text="hi")

    runtime = MagicMock(); runtime.step = runtime_step_stub
    registry = MagicMock(); registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    # send_text failed, send_template succeeded with reengagement
    assert adapter.sent_messages == []
    assert len(adapter.sent_templates) == 1
    sent = adapter.sent_templates[0]
    assert sent[0] == "+5511999"
    assert sent[1] == "reengagement_v1"
    assert sent[2] == "pt_BR"
    # params rendered: "amigo" (collected.nome was missing)
    assert sent[3] == ["amigo"]

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(inbound)
    assert inbound.status == "processed"
    assert "window_expired" in (inbound.error_detail or "")
    assert "recovered" in inbound.error_detail
```

Create `tests/integration/test_window_expired_no_template_fallback.py`:

```python
"""WindowExpiredError without reengagement_template configured → marks error, no template sent."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import WindowExpiredError
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.integration


def _tenant_yaml_no_reengagement(slug: str) -> str:
    return f"""id: {slug}
display_name: {slug.title()}
timezone: UTC
llm:
  default:
    provider: anthropic
    model: claude-sonnet-4-6
    api_key_ref: anthropic_key
messaging:
  provider: fake
"""


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_window_expired_no_template_marks_error(
    db_session, isolated_tenants_dir, session_factory, monkeypatch
) -> None:
    from ai_sdr.settings import get_settings
    monkeypatch.setattr(get_settings(), "tenants_dir", str(isolated_tenants_dir))

    tenant = Tenant(slug=f"wen_{uuid.uuid4().hex[:6]}", display_name="WEN")
    db_session.add(tenant)
    await db_session.flush()

    (isolated_tenants_dir / tenant.slug).mkdir()
    (isolated_tenants_dir / tenant.slug / "tenant.yaml").write_text(
        _tenant_yaml_no_reengagement(tenant.slug)
    )

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"x_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound)
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(WindowExpiredError("window expired"))
    runtime = MagicMock()
    async def step_stub(*a, **kw): return MagicMock(response_text="hi")
    runtime.step = step_stub
    registry = MagicMock(); registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    assert adapter.sent_messages == []
    assert adapter.sent_templates == []        # no template sent

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    await db_session.refresh(inbound)
    assert inbound.status == "error"
    assert "window_expired" in (inbound.error_detail or "")
```

- [ ] **Step 2: Modify the `except WindowExpiredError` branch**

Open `src/ai_sdr/worker/jobs/inbound.py`. Find the `except WindowExpiredError as e:` block in `_process_one`. Replace it with:

```python
                except WindowExpiredError as e:
                    # P9: try the tenant's reengagement_template fallback.
                    tenant_cfg = adapter_registry._tenant_loader.load(tenant.slug)  # type: ignore[attr-defined]
                    # Above relies on registry exposing its tenant_loader. If the
                    # registry doesn't expose it, fall back to loading directly:
                    #   from ai_sdr.tenant_loader.loader import TenantLoader
                    #   tenant_cfg = TenantLoader(Path(get_settings().tenants_dir)).load(tenant.slug)
                    reeng = (
                        tenant_cfg.messaging.reengagement_template
                        if tenant_cfg.messaging is not None
                        else None
                    )
                    if reeng is not None:
                        try:
                            # Render params with current TalkFlow.collected (empty for v1 if
                            # checkpointer hasn't been queried — Plan 9 keeps collected={} here;
                            # P10 may wire LangGraph state lookup if needed).
                            from ai_sdr.follow_up.jinja import render_params
                            params = render_params(
                                reeng.params,
                                lead=lead,
                                tenant=tenant,
                                collected={},
                            )
                            await adapter.send_template(
                                to=msg.from_address,
                                template_ref=reeng.template_ref,
                                language=reeng.language,
                                params=params,
                            )
                            msg.status = "processed"
                            msg.processed_at = datetime.now(UTC)
                            msg.error_detail = (
                                f"window_expired; recovered via reengagement template"
                            )
                            talkflow.last_agent_message_at = datetime.now(UTC)
                            log.info(
                                "messaging.window_expired_recovered",
                                lead_id=str(lead.id),
                            )
                        except Exception as e2:
                            msg.status = "error"
                            msg.error_detail = f"window_expired; reengagement failed: {e2}"
                            log.warning(
                                "messaging.reengagement_failed",
                                lead_id=str(lead.id),
                                err=str(e2),
                            )
                    else:
                        msg.status = "error"
                        msg.error_detail = f"window_expired: {e}"
                        log.warning(
                            "messaging.window_expired_no_template",
                            lead_id=str(lead.id),
                        )
                    await db.commit()
                    return
```

**Note on the registry tenant_loader access:** in P5, the AdapterRegistry doesn't expose its loader externally. **Choose ONE approach** depending on what's cleaner for your codebase:
- Add a `tenant_loader` property to `AdapterRegistry` and access via `adapter_registry.tenant_loader.load(tenant.slug)`.
- Instantiate a fresh `TenantLoader(Path(settings.tenants_dir))` inline (cheap — file YAML load).

Pick the second (no API change). Replace the `tenant_cfg = ...` line above with:

```python
                    from ai_sdr.tenant_loader.loader import TenantLoader
                    from ai_sdr.settings import get_settings
                    from pathlib import Path
                    tenant_cfg = TenantLoader(Path(get_settings().tenants_dir)).load(tenant.slug)
```

- [ ] **Step 3: Run tests**

Run on VPS: `uv run pytest tests/integration/test_window_expired_recovery.py tests/integration/test_window_expired_no_template_fallback.py -v`

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_window_expired_recovery.py tests/integration/test_window_expired_no_template_fallback.py
git commit -m "$(cat <<'EOF'
feat(plan9 t12): process_lead_inbox recovers from WindowExpiredError

When adapter.send_text raises WindowExpiredError (lead silent >24h),
the worker falls back to adapter.send_template using the tenant's
reengagement_template config. Renders params via Jinja sandbox.

If tenant has no reengagement_template configured, marks msg error
(P5 baseline behavior preserved as fallback).

Spec §1 Q5 — option A with B as fallback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: `follow_up_scanner` cron job + `_fire_follow_up`

**Files:**
- Create: `src/ai_sdr/worker/jobs/follow_up_scanner.py`
- Create: `tests/integration/test_follow_up_scanner_basic.py`
- Create: `tests/integration/test_follow_up_scanner_race_belt.py`

**Design:** The scanner queries due `pending` jobs across all tenants (with `SET LOCAL row_security = off`), then dispatches each via `_fire_follow_up`. Per-job logic uses the per-lead advisory lock, race-belt, error classification per spec §6.3.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_follow_up_scanner_basic.py`:

```python
"""Scanner picks only due pending jobs, ignores future/cancelled/completed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner

pytestmark = pytest.mark.integration


_YAML = """
id: t1
version: 1.0.0
entry_node: n1
nodes: {n1: {prompt: hi}}
follow_up:
  enabled: true
  max_attempts: 2
  sequence:
    - after: PT1H
      template_ref: t1
    - after: PT2H
      template_ref: t2
"""


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"sb_{uuid.uuid4().hex[:6]}", display_name="SB")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64, content_yaml=_YAML,
    )
    db_session.add(tv)
    await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_agent_message_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(tf)
    await db_session.commit()
    return tenant, tf, lead


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_scanner_fires_only_due_pending(db_session, session_factory) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    # 1 due + 1 future + 1 cancelled
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    ))
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=2, scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    ))
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=2, scheduled_at=datetime.now(UTC) - timedelta(minutes=5),
        status="cancelled",
    ))
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock(); registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    # Exactly one template sent (the due pending)
    assert len(adapter.sent_templates) == 1
    assert adapter.sent_templates[0][1] == "t1"

    # State updates
    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    jobs = (await db_session.execute(
        select(FollowUpJob).where(FollowUpJob.lead_id == lead.id)
        .order_by(FollowUpJob.attempt_number, FollowUpJob.created_at)
    )).scalars().all()
    completed = [j for j in jobs if j.status == "completed"]
    pending = [j for j in jobs if j.status == "pending"]
    cancelled = [j for j in jobs if j.status == "cancelled"]
    assert len(completed) == 1               # attempt 1 just fired
    assert len(pending) == 2                 # original future + newly-scheduled attempt 2
    assert len(cancelled) == 1               # untouched
```

Create `tests/integration/test_follow_up_scanner_race_belt.py`:

```python
"""Race belt: scheduler.last_lead_message_at > job.scheduled_at → cancel, don't send."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner

pytestmark = pytest.mark.integration


_YAML = """
id: t1
version: 1.0.0
entry_node: n1
nodes: {n1: {prompt: hi}}
follow_up:
  enabled: true
  max_attempts: 1
  sequence:
    - after: PT1H
      template_ref: t1
"""


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_lead_responded_after_scheduling_cancels_job(
    db_session, session_factory
) -> None:
    tenant = Tenant(slug=f"rb_{uuid.uuid4().hex[:6]}", display_name="RB")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64, content_yaml=_YAML,
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    # Job scheduled at T0, but lead responded at T0+10s.
    job_scheduled_at = datetime.now(UTC) - timedelta(minutes=5)
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_lead_message_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    db_session.add(tf)
    await db_session.flush()
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=job_scheduled_at, status="pending",
    ))
    await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock(); registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    # Race-belt fires: job cancelled, no template sent
    assert adapter.sent_templates == []
    await set_tenant_context(db_session, tenant.id)
    job = (await db_session.execute(select(FollowUpJob))).scalar_one()
    assert job.status == "cancelled"
    assert "responded" in (job.error_detail or "")
```

- [ ] **Step 2: Implement the scanner**

Create `src/ai_sdr/worker/jobs/follow_up_scanner.py`:

```python
"""follow_up_scanner — arq cron job, runs every 60s.

Picks all due `pending` follow_up_jobs across tenants and dispatches each
via _fire_follow_up. Per-job uses the same per-lead pg_advisory_lock that
process_lead_inbox uses (serializes scanner against the inbound worker).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.jinja import render_params
from ai_sdr.follow_up.scheduler import mark_cold_if_exhausted, schedule_next_followup
from ai_sdr.follow_up.treeflow_loader import load_treeflow_follow_up
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
)
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant

log = structlog.get_logger(__name__)

_BATCH_SIZE = 200


def _stable_lock_key(tenant_id: str, lead_id: str) -> int:
    """Same hash function as process_lead_inbox — ensures the two paths
    serialize via the same Postgres advisory lock."""
    h = hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).digest()
    return int.from_bytes(h[:8], "big", signed=False) & 0x7FFFFFFFFFFFFFFF


async def follow_up_scanner(ctx: dict[str, Any]) -> None:
    """arq.cron entrypoint. Runs every 60s."""
    session_factory = ctx["session_factory"]
    registry = ctx["adapter_registry"]

    async with session_factory() as db:
        # Cross-tenant scan — bypass RLS for this read only.
        await db.execute(text("SET LOCAL row_security = off"))
        rows = (await db.execute(
            select(FollowUpJob.id, FollowUpJob.tenant_id, FollowUpJob.lead_id)
            .where(
                FollowUpJob.status == "pending",
                FollowUpJob.scheduled_at <= func.now(),
            )
            .order_by(FollowUpJob.scheduled_at.asc())
            .limit(_BATCH_SIZE)
        )).all()

    log.info("follow_up.scanner.batch", count=len(rows))
    for row in rows:
        try:
            await _fire_follow_up(
                session_factory, registry,
                row.id, row.tenant_id, row.lead_id,
            )
        except Exception:
            log.exception("follow_up.scanner.job_failed", job_id=str(row.id))


async def _fire_follow_up(
    session_factory,
    registry,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Single-job dispatch. Per-lead advisory lock, race-belt, error
    classification per spec §6.3."""
    lock_key = _stable_lock_key(str(tenant_id), str(lead_id))

    async with session_factory() as db:
        await set_tenant_context(db, tenant_id)

        got = (await db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
        )).scalar()
        if not got:
            log.info("follow_up.lock_contention", lead_id=str(lead_id))
            return

        try:
            job = await db.get(FollowUpJob, job_id)
            if job is None or job.status != "pending":
                return

            talkflow = await db.get(TalkFlow, job.talkflow_id)
            lead = await db.get(Lead, job.lead_id)
            tenant = await db.get(Tenant, job.tenant_id)
            if talkflow is None or lead is None or tenant is None:
                job.status = "cancelled"
                job.error_detail = "missing parent row"
                await db.commit()
                return

            # Race-belt
            if talkflow.last_lead_message_at and talkflow.last_lead_message_at > job.scheduled_at:
                job.status = "cancelled"
                job.error_detail = "lead responded after scheduling"
                await db.commit()
                return
            if talkflow.status in ("cold", "completed"):
                job.status = "cancelled"
                job.error_detail = f"talkflow {talkflow.status}"
                await db.commit()
                return

            tf_config = await load_treeflow_follow_up(db, talkflow)
            if tf_config is None or not tf_config.enabled:
                job.status = "cancelled"
                job.error_detail = "treeflow follow_up disabled"
                await db.commit()
                return

            try:
                step = tf_config.sequence[job.attempt_number - 1]
            except IndexError:
                job.status = "error"
                job.error_detail = (
                    f"attempt_number {job.attempt_number} > sequence length "
                    f"{len(tf_config.sequence)}"
                )
                await db.commit()
                return

            params = render_params(
                step.params,
                lead=lead, tenant=tenant, collected={},
            )
            adapter = registry.get(tenant, "whatsapp_cloud")

            try:
                result = await adapter.send_template(
                    to=lead.whatsapp_e164,
                    template_ref=step.template_ref,
                    language=step.language,
                    params=params,
                )
            except RecipientUnreachable as e:
                lead.status = "unreachable"
                lead.unreachable_reason = str(e)
                await db.execute(
                    update(FollowUpJob)
                    .where(
                        FollowUpJob.lead_id == lead.id,
                        FollowUpJob.status == "pending",
                    )
                    .values(status="cancelled", error_detail="lead unreachable")
                )
                job.status = "error"
                job.error_detail = f"unreachable: {e}"
                log.warning("follow_up.recipient_unreachable", lead_id=str(lead.id))
                await db.commit()
                return
            except (AuthError, PolicyError, MessagingError) as e:
                job.status = "error"
                job.error_detail = f"{type(e).__name__}: {e}"
                log.error(
                    "follow_up.send_failed",
                    lead_id=str(lead.id),
                    err_type=type(e).__name__,
                    err=str(e),
                )
                await db.commit()
                return

            # Success
            job.status = "completed"
            job.fired_at = datetime.now(UTC)
            job.sent_external_id = result.external_id
            talkflow.last_agent_message_at = datetime.now(UTC)
            talkflow.follow_up_attempt_number = job.attempt_number

            became_cold = mark_cold_if_exhausted(talkflow, tf_config, job.attempt_number)
            if became_cold:
                log.info(
                    "follow_up.exhausted_marked_cold",
                    talkflow_id=str(talkflow.id),
                    attempts=job.attempt_number,
                )
            else:
                await schedule_next_followup(
                    db, talkflow, lead, tenant, tf_config,
                    next_attempt_number=job.attempt_number + 1,
                )

            await db.commit()
        finally:
            try:
                await db.rollback()
            except Exception:
                pass
            await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            await db.commit()
```

- [ ] **Step 3: Run tests**

Run on VPS: `uv run pytest tests/integration/test_follow_up_scanner_basic.py tests/integration/test_follow_up_scanner_race_belt.py -v`

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/follow_up_scanner.py tests/integration/test_follow_up_scanner_basic.py tests/integration/test_follow_up_scanner_race_belt.py
git commit -m "$(cat <<'EOF'
feat(plan9 t13): follow_up_scanner cron + per-job firing logic

Scanner picks all due pending jobs across tenants (SET LOCAL
row_security=off), dispatches each via _fire_follow_up. Per-lead
pg_advisory_lock shared with process_lead_inbox serializes the two
paths. Race-belt rejects jobs where lead.last_lead_message_at >
job.scheduled_at. Errors mapped: RecipientUnreachable cascade-cancels
all pending; Auth/Policy/Messaging mark job error.

Spec §6.2 + §6.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: arq cron registration in `worker/main.py`

**Files:**
- Modify: `src/ai_sdr/worker/main.py`

**Design:** Add `cron(follow_up_scanner, ...)` to `WorkerSettings.cron_jobs`. Runs at every minute (60s cadence).

- [ ] **Step 1: Edit worker/main.py**

Open `src/ai_sdr/worker/main.py`. Add the import:

```python
from arq import cron
from ai_sdr.worker.jobs.follow_up_scanner import follow_up_scanner
```

In `WorkerSettings`, add (or extend if `cron_jobs` already exists):

```python
class WorkerSettings:
    functions = [process_lead_inbox]
    cron_jobs = [
        cron(follow_up_scanner, minute=set(range(0, 60)), run_at_startup=False),
    ]
    # ... rest unchanged ...
```

`minute=set(range(0, 60))` = every minute. `run_at_startup=False` = don't fire immediately on worker boot.

- [ ] **Step 2: Smoke-test worker import**

Run: `uv run python -c "from ai_sdr.worker.main import WorkerSettings; print(WorkerSettings.functions); print(WorkerSettings.cron_jobs)"`

Expected: prints the functions list + cron_jobs list (1 entry).

- [ ] **Step 3: Smoke-test CLI**

Run: `uv run ai-sdr worker --help`

Expected: typer help, no ImportError.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/main.py
git commit -m "$(cat <<'EOF'
feat(plan9 t14): register follow_up_scanner cron in WorkerSettings

Runs at every minute. run_at_startup=False to avoid double-firing
right after a worker restart.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `ai-sdr follow-ups` CLI — list/cancel/dry-run

**Files:**
- Create: `src/ai_sdr/cli/follow_ups.py`
- Modify: `src/ai_sdr/cli/app.py`
- Create: `tests/integration/test_follow_ups_cli_integration.py`

**Design:** Three commands in one typer sub-app. Same session pattern as `cli/users.py` and `cli/simulate.py` (each command opens its own async engine).

- [ ] **Step 1: Create the CLI module**

Create `src/ai_sdr/cli/follow_ups.py`:

```python
"""`ai-sdr follow-ups` — operator visibility + manual control of scheduled HSM templates.

Commands:
  list   --tenant <slug> [--lead <uuid>] [--status pending|all|...]
  cancel --tenant <slug> --lead <uuid>
  dry-run --tenant <slug> --treeflow <id> --lead <uuid>

All commands open their own async engine (same pattern as ai-sdr users
and ai-sdr simulate). The CLI hits the DB directly — not via REST —
because follow_up ops are admin/dev surface, not user-facing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.follow_up.duration import parse_duration
from ai_sdr.follow_up.jinja import render_params
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.schemas.treeflow_yaml import TreeFlow
from ai_sdr.settings import get_settings

follow_ups_app = typer.Typer(help="Follow-up scheduler ops")
console = Console()


def _make_session():
    engine = create_async_engine(get_settings().database_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _load_tenant(session, slug: str) -> Tenant:
    t = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        console.print(f"[red]tenant not found: {slug}[/red]")
        raise typer.Exit(1)
    return t


@follow_ups_app.command("list")
def list_(
    tenant: Annotated[str, typer.Option("--tenant")],
    lead: Annotated[str | None, typer.Option("--lead", help="Filter to one lead UUID")] = None,
    status: Annotated[
        str, typer.Option("--status", help="pending | completed | cancelled | error | all")
    ] = "pending",
) -> None:
    asyncio.run(_list_async(tenant, lead, status))


async def _list_async(tenant_slug: str, lead_filter: str | None, status_filter: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        stmt = select(FollowUpJob).order_by(FollowUpJob.scheduled_at.asc())
        if status_filter != "all":
            stmt = stmt.where(FollowUpJob.status == status_filter)
        if lead_filter:
            stmt = stmt.where(FollowUpJob.lead_id == uuid.UUID(lead_filter))
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            console.print(f"[yellow]no follow-ups (status={status_filter!r})[/yellow]")
            await engine.dispose()
            return

        table = Table(title=f"Follow-up jobs — {tenant_slug} (status={status_filter})")
        table.add_column("ID", no_wrap=True)
        table.add_column("Lead")
        table.add_column("Attempt", justify="right")
        table.add_column("Scheduled")
        table.add_column("Status")
        table.add_column("Sent ID")
        for r in rows:
            table.add_row(
                str(r.id)[:8] + "…",
                str(r.lead_id)[:8] + "…",
                str(r.attempt_number),
                r.scheduled_at.strftime("%Y-%m-%d %H:%M"),
                r.status,
                (r.sent_external_id or "")[:14] + ("…" if r.sent_external_id and len(r.sent_external_id) > 14 else ""),
            )
        console.print(table)
    await engine.dispose()


@follow_ups_app.command("cancel")
def cancel(
    tenant: Annotated[str, typer.Option("--tenant")],
    lead: Annotated[str, typer.Option("--lead")],
) -> None:
    asyncio.run(_cancel_async(tenant, lead))


async def _cancel_async(tenant_slug: str, lead_id_str: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        lead_id = uuid.UUID(lead_id_str)
        result = await session.execute(
            update(FollowUpJob)
            .where(FollowUpJob.lead_id == lead_id, FollowUpJob.status == "pending")
            .values(status="cancelled", error_detail="manual cancel via CLI")
        )
        n = result.rowcount or 0
        await session.commit()
        if n == 0:
            console.print(f"[yellow]no pending follow-ups for lead {lead_id_str}[/yellow]")
        else:
            console.print(f"[green]cancelled {n} pending follow-up(s) for lead {lead_id_str}[/green]")
    await engine.dispose()


@follow_ups_app.command("dry-run")
def dry_run(
    tenant: Annotated[str, typer.Option("--tenant")],
    treeflow: Annotated[str, typer.Option("--treeflow")],
    lead: Annotated[str, typer.Option("--lead")],
) -> None:
    asyncio.run(_dry_run_async(tenant, treeflow, lead))


async def _dry_run_async(tenant_slug: str, treeflow_id: str, lead_id_str: str) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        tenant = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, tenant.id)
        lead = await session.get(Lead, uuid.UUID(lead_id_str))
        if lead is None:
            console.print(f"[red]lead not found: {lead_id_str}[/red]")
            raise typer.Exit(1)

        talkflow = (
            await session.execute(
                select(TalkFlow).where(
                    TalkFlow.tenant_id == tenant.id,
                    TalkFlow.lead_id == lead.id,
                )
            )
        ).scalar_one_or_none()
        if talkflow is None:
            console.print(f"[red]no TalkFlow for this lead in tenant {tenant_slug}[/red]")
            raise typer.Exit(1)

        # Load latest TreeflowVersion for the given treeflow_id
        tv = (
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
        if tv is None:
            console.print(f"[red]treeflow not found: {treeflow_id}[/red]")
            raise typer.Exit(1)

        parsed = TreeFlow.model_validate(yaml.safe_load(tv.content_yaml))
        cfg = parsed.follow_up
        if cfg is None or not cfg.enabled:
            console.print(f"[yellow]TreeFlow {treeflow_id} has no follow_up enabled[/yellow]")
            await engine.dispose()
            return

        next_attempt = talkflow.follow_up_attempt_number + 1
        if next_attempt > cfg.max_attempts:
            console.print(
                f"[yellow]talkflow already at attempt {talkflow.follow_up_attempt_number} "
                f"(max={cfg.max_attempts}) — would mark cold, no send[/yellow]"
            )
            await engine.dispose()
            return

        step = cfg.sequence[next_attempt - 1]
        params = render_params(step.params, lead=lead, tenant=tenant, collected={})
        scheduled_at = datetime.now(UTC) + parse_duration(step.after)

        table = Table(title=f"Dry-run — next follow-up for lead {lead_id_str}")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("attempt_number", str(next_attempt))
        table.add_row("template_ref", step.template_ref)
        table.add_row("language", step.language)
        table.add_row("params (rendered)", str(params))
        table.add_row("scheduled_at (if scheduled now)", scheduled_at.strftime("%Y-%m-%d %H:%M UTC"))
        console.print(table)
        console.print("[dim](dry-run — nothing sent, nothing inserted)[/dim]")
    await engine.dispose()
```

- [ ] **Step 2: Register the sub-app**

Open `src/ai_sdr/cli/app.py`. Add:

```python
from ai_sdr.cli.follow_ups import follow_ups_app
# ...
app.add_typer(follow_ups_app, name="follow-ups")
```

- [ ] **Step 3: Smoke**

Run: `uv run ai-sdr follow-ups list --help && uv run ai-sdr follow-ups cancel --help && uv run ai-sdr follow-ups dry-run --help`

Expected: typer help for all three commands.

- [ ] **Step 4: Write integration test for the cancel command**

Create `tests/integration/test_follow_ups_cli_integration.py`:

```python
"""ai-sdr follow-ups cancel — hits real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.follow_up_job import FollowUpJob
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration

runner = CliRunner()


async def test_cancel_marks_pending_as_cancelled(db_session) -> None:
    tenant = Tenant(slug=f"cli_{uuid.uuid4().hex[:6]}", display_name="C")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead)
    await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    db_session.add(FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1, scheduled_at=datetime.now(UTC) + timedelta(hours=1),
        status="pending",
    ))
    await db_session.commit()

    r = runner.invoke(
        app,
        ["follow-ups", "cancel", "--tenant", tenant.slug, "--lead", str(lead.id)],
    )
    assert r.exit_code == 0
    assert "cancelled 1" in r.output

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    job = (await db_session.execute(select(FollowUpJob).where(FollowUpJob.lead_id == lead.id))).scalar_one()
    assert job.status == "cancelled"
    assert "manual" in (job.error_detail or "")
```

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/cli/follow_ups.py src/ai_sdr/cli/app.py tests/integration/test_follow_ups_cli_integration.py
git commit -m "$(cat <<'EOF'
feat(plan9 t15): ai-sdr follow-ups list/cancel/dry-run CLI

list:    table of jobs filtered by status (default pending) and lead.
cancel:  bulk UPDATE pending → cancelled for a lead.
dry-run: shows what the next attempt would be (template_ref, params
         rendered, scheduled_at) without sending or inserting.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Adapter-compliance suite extension

**Files:**
- Modify: `tests/integration/test_adapter_compliance.py`

**Design:** Plan 5 T24 created a parametrized suite that runs the SAME contract tests against `[fake, whatsapp_cloud_mocked]`. P9 adds `send_template` to the contract; the suite must cover it for both adapters so any future impl (Vialum) automatically inherits coverage.

- [ ] **Step 1: Append send_template tests**

Open `tests/integration/test_adapter_compliance.py`. Add THREE new tests at the bottom of the file:

```python
async def test_send_template_returns_external_id(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, WhatsAppCloudAPIAdapter):
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200,
                        json={"messages": [{"id": "wamid.TPL_OUT="}]},
                    )
                ),
                timeout=15.0,
            ),
        )
    r = await adapter.send_template(
        to=helpers["expected_from_address"],
        template_ref="any_template_v1",
        language="pt_BR",
        params=["x"],
    )
    assert isinstance(r, SendResult)
    assert r.external_id


async def test_send_template_raises_recipient_unreachable(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, FakeMessagingAdapter):
        adapter.fail_next_template_send(RecipientUnreachable("not on WA"))
    else:
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        400, json={"error": {"code": 131026, "message": "not on WA"}}
                    )
                ),
                timeout=15.0,
            ),
        )
    with pytest.raises(RecipientUnreachable):
        await adapter.send_template(
            to=helpers["expected_from_address"],
            template_ref="x", language="pt_BR", params=[],
        )


async def test_send_template_raises_auth_error_on_401(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, FakeMessagingAdapter):
        from ai_sdr.messaging.errors import AuthError
        adapter.fail_next_template_send(AuthError("bad token"))
    else:
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(401, json={"error": {"code": 190}})
                ),
                timeout=15.0,
            ),
        )
    from ai_sdr.messaging.errors import AuthError
    with pytest.raises(AuthError):
        await adapter.send_template(
            to=helpers["expected_from_address"],
            template_ref="x", language="pt_BR", params=[],
        )
```

(Imports — if not already in the file: `from ai_sdr.messaging.errors import RecipientUnreachable`, `from ai_sdr.messaging.fake import FakeMessagingAdapter`, etc.)

- [ ] **Step 2: Run on VPS**

Run: `uv run pytest tests/integration/test_adapter_compliance.py -v`

Expected: existing tests still pass + 3 new × 2 params = 6 new test executions.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_adapter_compliance.py
git commit -m "$(cat <<'EOF'
test(plan9 t16): adapter-compliance suite extension for send_template

3 new contract tests parametrized over [fake, whatsapp_cloud_mocked]:
- send_template returns external_id
- send_template raises RecipientUnreachable on 400/131026
- send_template raises AuthError on 401

Future Vialum/other adapter impls automatically inherit coverage by
joining the params list.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Wiring — tenants/example, CLAUDE.md

**Files:**
- Modify: `tenants/example/tenant.yaml`
- Modify: `tenants/example/treeflows/example.yaml`
- Modify: `CLAUDE.md`

**Design:** Make the example tenant exercise both mechanics so `ai-sdr simulate` and live tests can demonstrate Plan 9 immediately.

- [ ] **Step 1: Update tenants/example/tenant.yaml**

Open `tenants/example/tenant.yaml`. Find the `messaging:` block. Add (commented — opt-in when a real template is registered in Meta):

```yaml
messaging:
  # ... existing fields ...
  # reengagement_template:
  #   template_ref: "reengagement_default_v1"     # must be pre-approved in Meta Business Manager
  #   language: "pt_BR"
  #   params:
  #     - "{{ collected.nome | default('amigo') }}"
```

Leave commented because the example tenant doesn't actually have a Meta-registered template by default. Operators uncomment + edit when going live.

- [ ] **Step 2: Update tenants/example/treeflows/example.yaml**

Open `tenants/example/treeflows/example.yaml`. **First bump the version** (e.g., from `1.0.0` to `1.1.0`) — runtime refuses to re-publish the same version with a different content_hash. Then add at the bottom (sibling to `nodes:`):

```yaml
follow_up:
  enabled: false                  # leave disabled in the example so dev runs don't schedule jobs
  max_attempts: 2
  sequence:
    - after: "PT24H"
      template_ref: "followup_24h_v1"
      language: "pt_BR"
      params:
        - "{{ collected.nome | default('amigo') }}"
    - after: "P3D"
      template_ref: "followup_72h_v1"
      language: "pt_BR"
      params:
        - "{{ collected.nome | default('amigo') }}"
```

`enabled: false` keeps the example tenant from scheduling jobs against unregistered templates. Operators flip to true once they've registered both `followup_24h_v1` and `followup_72h_v1` in Meta.

- [ ] **Step 3: Update CLAUDE.md**

Open `CLAUDE.md`. Append a new section at the end:

````markdown
## Follow-up + HSM templates (Plano 9)

- **Two mechanics**:
  1. *Proactive scheduled follow-up* — when lead goes silent, `arq.cron follow_up_scanner` fires HSM templates per `treeflow.follow_up.sequence`. Reset when lead responds; mark `talkflow.status='cold'` after `max_attempts`.
  2. *Reactive WindowExpired recovery* — when `send_text` raises `WindowExpiredError`, worker falls back to `tenant.messaging.reengagement_template`. If absent, marks msg error (P5 baseline).

- **TreeFlow YAML config** (per funnel):
  ```yaml
  follow_up:
    enabled: true
    max_attempts: 3
    sequence:
      - after: "PT24H"                   # ISO-8601 duration
        template_ref: "followup_24h_v1"   # name registered + approved in Meta Business Manager
        language: "pt_BR"
        params:
          - "{{ collected.nome | default('amigo') }}"
  ```

  `enabled=true` requires `len(sequence) >= max_attempts`. Templates referenced by name only — Meta is source-of-truth for the actual approved text.

- **Tenant YAML config** (reengagement only):
  ```yaml
  messaging:
    provider: whatsapp_cloud
    # ...
    reengagement_template:
      template_ref: "reengagement_default_v1"
      language: "pt_BR"
      params:
        - "{{ collected.nome | default('amigo') }}"
  ```

- **Template params**: rendered with Jinja2 `SandboxedEnvironment` against:
  - `collected.<field>` — TalkFlow's extracted fields (v1 passes `{}` because collected state lookup hooks into LangGraph checkpointer — caller passes empty dict for now; P10 may wire fuller context)
  - `lead.whatsapp_e164`, `lead.external_label`
  - `tenant.slug`, `tenant.display_name`
  - Filters: `default`, `lower`, `upper`, `trim`, `truncate(N)`. `StrictUndefined` forces explicit defaults.

- **Schedule semantics**: timer starts at `talkflow.last_agent_message_at`. Lead inbound resets counter + cancels pending + reactivates cold. Scanner runs every 60s; per-lead `pg_advisory_lock` (same hash as `process_lead_inbox`) serializes scanner vs worker. Race-belt at fire time checks `talkflow.last_lead_message_at > job.scheduled_at`.

- **Schedule-one-at-a-time**: each fired job inserts the next attempt's row. Config changes in `treeflow.yaml` apply to subsequent in-flight schedules naturally. Requires bumping the TreeFlow `version` to publish a new content_hash.

- **CLI ops**:
  ```bash
  ai-sdr follow-ups list --tenant <slug> [--lead <uuid>] [--status pending|completed|cancelled|error|all]
  ai-sdr follow-ups cancel --tenant <slug> --lead <uuid>
  ai-sdr follow-ups dry-run --tenant <slug> --treeflow <id> --lead <uuid>
  ```

- **Cold lead reactivation**: a `talkflow.status='cold'` lead that receives an inbound is automatically flipped back to `'active'` by `process_lead_inbox`; attempt counter resets to 0; new follow-up scheduled after agent's reply.

- **WhatsApp HSM payload**: Meta API endpoint `POST /messages` with `type=template`. Body params are positional (`{{1}}, {{2}}, ...` in the Meta-registered template), filled from `params` list at send time. Same retry stack (tenacity 3 attempts, exp backoff) and error classification (`_classify_error`) as `send_text`.

- **Migration**: `0010_follow_up_and_talkflow_columns` — `follow_up_jobs` table (RLS, partial indexes) + 3 columns on `talkflows`.

- **Setting up a tenant for live follow-up**:
  1. Register HSM templates in Meta Business Manager. Note the exact `name` strings.
  2. Edit `tenants/<slug>/treeflows/<id>.yaml`: add the `follow_up:` block with matching `template_ref`s. Bump `version` (semver).
  3. (Optional) Edit `tenants/<slug>/tenant.yaml` `messaging.reengagement_template` for WindowExpired recovery.
  4. Restart worker: `docker compose up -d --build worker`. The cron registers on startup.
````

- [ ] **Step 4: Commit**

```bash
git add tenants/example/tenant.yaml tenants/example/treeflows/example.yaml CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(plan9 t17): wire example tenant + CLAUDE.md operator guide

example tenant: reengagement_template commented (template not registered
in Meta — opt-in when operator has one). example treeflow gains
follow_up section with enabled=false (operator flips to true after
registering templates). version bumped to publish.

CLAUDE.md: new "Follow-up + HSM templates (Plano 9)" section with
config shapes, semantics, CLI, and operator setup steps.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final task: Plano 9 close-out

- [ ] **Step 1: Run full unit suite locally**

```bash
make lint && make format && make type && make test-unit
```

Expected: all green.

- [ ] **Step 2: Push branch + validate integration on VPS**

```bash
git push -u origin dev/nicolas-p9

ssh vps-nova 'export PATH=/root/.local/bin:$PATH && cd /root/PeSDR && git fetch origin && git checkout dev/nicolas-p9 && uv sync && uv run alembic upgrade head && uv run pytest tests/integration -q'
```

Expected: integration suite green (known noise: 6 P5 flakes, 5 P4a auth-skipped). No NEW failures.

- [ ] **Step 3: Smoke the worker boot**

On the VPS:
```bash
docker compose up -d --build worker
docker compose logs --tail=20 worker
```

Expected: logs show `worker.starting`, `checkpointer.ready`, `worker.ready`, then quiet (no cron firing yet because no due jobs).

- [ ] **Step 4: Tag the close-out commit**

```bash
git commit --allow-empty -m "$(cat <<'EOF'
chore(plan9): close-out — all 17 tasks landed

Follow-up scheduler + WhatsApp HSM templates live. Two paths:
- Proactive: arq.cron follow_up_scanner every 60s fires HSM
  templates per TreeFlow.follow_up.sequence. Resets on inbound;
  cold-marks at max_attempts; reactivates cold on inbound.
- Reactive: process_lead_inbox catches WindowExpiredError and
  falls back to tenant.messaging.reengagement_template.

MessagingAdapter contract extended additively with send_template.
WhatsApp Cloud impl + Fake impl + adapter-compliance suite updated.
CLI ai-sdr follow-ups (list/cancel/dry-run) for ops.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Notes for plan execution

- **Migration 0010 must follow 0009 (P11) when both ship to the same trunk**. If P9 ships first (P11 not yet merged), update `down_revision` to `'0008_talkflows_lead_id_fk'`. Coordinator handles this at merge time.
- **Task 4's `_check_iso_duration` validator depends on Task 5's `parse_duration`** — implement Task 5 BEFORE Task 4 Step 4 (running the schema tests). The plan numbering puts Task 4 first because schemas are "data layer", but the lazy import inside the validator means Task 5 must exist before validation runs.
- **`process_lead_inbox` modifications (Tasks 11 + 12) are surgical insertions** into an existing file. Read the current shape FIRST. Insertions in Task 11 go BEFORE the drain loop (cancellation block) and INSIDE the drain loop after send_text success. Task 12's WindowExpired block REPLACES the existing `except WindowExpiredError` body — confirm the existing one is just `msg.status='error'; log; return`.
- **The scanner test setup (T13) requires `last_agent_message_at` to be set on the seeded talkflow**. Without it, the race-belt comparison is None vs scheduled_at — None is treated as "not yet responded" so jobs fire. That's intentional (first send schedules the first follow-up; if last_agent_message_at is None, that means the agent never sent, so no follow-up should exist — pathological case).
- **Reuse pyproject.toml conventions**: `bcrypt`, `arq`, `tenacity`, `httpx`, `langchain-*` already there. Just add `isodate`. Don't reorder existing entries.
- **Skip integration runs locally** when Docker isn't available — push and let the VPS validate.

