# FlowEngine FE-01a — Schema Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land all new database tables, model classes, and Pydantic schemas required by the FlowEngine architecture, without implementing any pipeline runtime. After this plan: every persistence concept the FlowEngine touches (Lead identity extensions, Talks, TalkFlowStates, Events, and reserved-slot tables for Sentinel/A-B/HITL/Adapters) exists in the database with RLS + indexes, and the Pydantic shapes (`TurnDecision`, `HumanEscalation`, `JudgeVerdict`, `Message`, `ActiveTreatment`) are importable from `ai_sdr.flowengine`. Old pipeline (LangGraph) still runs unchanged.

**Architecture:** Migrations 0012–0022 add the new tables. Each new table is tenant-scoped (RLS enabled via `app.current_tenant` setting), uses UUID PKs with `uuid_generate_v4()` server default, and follows the existing `outbound_messages` migration style. The Lead identity is added to the existing `leads` table (no new `users` table — the P11 `users` table holds operators and must not be touched). Reserved-slot tables (experiments, response_reviews, sentinel_reviews, adapter_calls, treeflow_improvement_suggestions) get their schema now so FE-04..FE-07 can write rows without further DDL. New Pydantic models live in a fresh `src/ai_sdr/flowengine/` package; legacy code untouched.

**Tech Stack:** SQLAlchemy 2.0 typed Mapped[] columns, Alembic migrations, asyncpg + Postgres 16, Pydantic v2, pytest + pytest-asyncio. Existing patterns from `outbound_messages` migration (0011) and `Lead` model are the templates.

**Source spec:** `docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md` — §3 (conceptual model), §22 (events table), §25 (experiments), §28 (consolidated migrations list).

**Out of scope for this plan:**
- Pipeline orchestrator function (FE-01b)
- Layered system prompt builder (FE-01b)
- Routing/transition validation (FE-01b)
- Python critic validator replacement (FE-01b)
- Any runtime behavior that uses the new tables (Sentinel, escalation, adapter dispatch — those are FE-04+)
- LangGraph removal (FE-02)
- TreeFlow YAML v2 parsing (FE-03)

---

## File Structure

### Files created

```
migrations/versions/
  0012_extend_leads_with_identity_fields.py
  0013_create_talks_table.py
  0014_create_talkflow_states_table.py
  0015_create_events_table.py
  0016_create_experiments_table.py
  0017_create_response_reviews_table.py
  0018_create_sentinel_reviews_table.py
  0019_create_adapter_calls_table.py
  0020_create_treeflow_improvement_suggestions_table.py
  0021_extend_outbound_messages_with_media.py
  0022_extend_inbound_messages_with_media.py

src/ai_sdr/flowengine/
  __init__.py            — package marker
  state.py               — Pydantic: Message, ActiveTreatment, ObjectionHistoryEntry, StackFrame
  decision.py            — Pydantic: TurnDecision, HumanEscalation, JudgeVerdict

src/ai_sdr/models/
  talk.py                — Talk SQLAlchemy model + TalkStatus + HandlingMode
  talkflow_state.py      — TalkFlowState SQLAlchemy model
  event.py               — Event SQLAlchemy model

src/ai_sdr/repositories/
  __init__.py            — package marker (if not already present)
  lead_repository.py     — Lead CRUD with new identity fields
  talk_repository.py     — Talk CRUD + lookup by (tenant, lead, status)
  talkflow_state_repository.py — TalkFlowState CRUD + message append

tests/unit/
  test_flowengine_decision_schema.py   — TurnDecision/HumanEscalation/JudgeVerdict validation
  test_flowengine_state_schema.py      — Message/ActiveTreatment/ObjectionHistoryEntry validation

tests/integration/
  test_migration_0012_leads_extension.py
  test_migration_0013_talks.py
  test_migration_0014_talkflow_states.py
  test_migration_0015_events.py
  test_migration_0016_to_0020_reserved_slots.py
  test_migration_0021_outbound_media.py
  test_migration_0022_inbound_media.py
  test_lead_repository.py
  test_talk_repository.py
  test_talkflow_state_repository.py
  test_all_migrations_apply_clean.py
```

### Files modified

```
src/ai_sdr/models/lead.py        — add 9 identity fields
src/ai_sdr/models/tenant.py      — add architecture_version column
src/ai_sdr/models/outbound_message.py — add media fields
src/ai_sdr/models/inbound_message.py  — add media fields
```

### Reserved (NOT touched in this plan)

```
src/ai_sdr/treeflow/             — LangGraph code (FE-02 deletes)
src/ai_sdr/guardrails/critic.py  — kept alive (FE-02 deletes alongside LangGraph)
src/ai_sdr/workers/jobs/inbound.py — pipeline v1 unchanged (FE-01b adds the v2 branch)
src/ai_sdr/models/user.py        — P11 operators table, do NOT modify
src/ai_sdr/models/user_tenant_access.py — do NOT modify
```

---

## Branch and worktree

This plan creates database migrations that affect every other developer's local DB once merged. Execute in an isolated git worktree per `superpowers:using-git-worktrees`. Suggested branch name: `dev/nicolas-fe01a-schema`, branched off `dev/nicolas`.

---

## Task 1: Create flowengine package namespace

**Files:**
- Create: `src/ai_sdr/flowengine/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_flowengine_package.py`:

```python
def test_flowengine_package_importable() -> None:
    """The flowengine package marker exists. Future modules live here."""
    import ai_sdr.flowengine  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicolasamaral/dev/PeSDR && python -m pytest tests/unit/test_flowengine_package.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.flowengine'`

- [ ] **Step 3: Create the package marker**

Create `src/ai_sdr/flowengine/__init__.py` with content:

```python
"""FlowEngine — single-LLM-call-per-turn conversational orchestrator.

Replaces the per-node LLM pattern (Plano 2, LangGraph-based) with a unified
state machine over Lead/Talk/TalkFlow + one structured-output LLM call per
inbound turn. See docs/superpowers/specs/2026-06-08-flow-engine-architecture-design.md.

FE-01a: schemas and migrations only — no runtime yet.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_flowengine_package.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/__init__.py tests/unit/test_flowengine_package.py
git commit -m "feat(flowengine): create flowengine package namespace"
```

---

## Task 2: Migration 0012 — extend leads with identity fields

**Files:**
- Create: `migrations/versions/0012_extend_leads_with_identity_fields.py`
- Create: `tests/integration/test_migration_0012_leads_extension.py`

This migration adds 9 columns to the existing `leads` table to carry the long-lived Lead identity per spec §3.1. No new table is created — collision with the P11 `users` table is avoided by extending `leads` instead.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0012_leads_extension.py`:

```python
"""Verifies migration 0012 added identity fields to leads table."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_leads_table_has_new_identity_columns(db_session: AsyncSession) -> None:
    """All 9 new columns exist with expected types and defaults."""
    result = await db_session.execute(
        text(
            """
            SELECT column_name, data_type, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'leads'
              AND column_name IN (
                  'channel_identifiers', 'display_name', 'profile',
                  'profile_last_updated', 'long_term_memory_enabled',
                  'risk_level', 'risk_level_since', 'risk_level_reason',
                  'acquisition_metadata'
              )
            ORDER BY column_name
            """
        )
    )
    rows = {r[0]: r for r in result.all()}
    assert set(rows.keys()) == {
        "channel_identifiers",
        "display_name",
        "profile",
        "profile_last_updated",
        "long_term_memory_enabled",
        "risk_level",
        "risk_level_since",
        "risk_level_reason",
        "acquisition_metadata",
    }
    assert rows["risk_level"][2] is not None  # has a default
    assert "normal" in rows["risk_level"][2]
    assert rows["long_term_memory_enabled"][2] is not None
    assert "false" in rows["long_term_memory_enabled"][2].lower()


@pytest.mark.asyncio
async def test_leads_risk_level_check_constraint_rejects_invalid(
    db_session: AsyncSession,
) -> None:
    """risk_level CHECK constraint blocks unknown values."""
    # First we need a tenant context
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :s, :n)"),
        {"id": tenant_id, "s": f"test-{tenant_id.hex[:8]}", "n": "test"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )

    with pytest.raises(Exception) as excinfo:
        await db_session.execute(
            text(
                "INSERT INTO leads (tenant_id, risk_level) "
                "VALUES (:tid, 'malicious_value')"
            ),
            {"tid": tenant_id},
        )
    assert "ck_leads_risk_level" in str(excinfo.value).lower() or "check" in str(
        excinfo.value
    ).lower()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_leads_accepts_full_identity_payload(db_session: AsyncSession) -> None:
    """Insert lead with all new fields populated; round-trip works."""
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :s, :n)"),
        {"id": tenant_id, "s": f"test-{tenant_id.hex[:8]}", "n": "test"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )
    lead_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO leads (
                id, tenant_id, channel_identifiers, display_name,
                profile, long_term_memory_enabled, risk_level,
                risk_level_reason, acquisition_metadata
            ) VALUES (
                :id, :tid, CAST(:ci AS JSONB), :dn,
                CAST(:p AS JSONB), :lt, :rl, :rr, CAST(:am AS JSONB)
            )
            """
        ),
        {
            "id": lead_id,
            "tid": tenant_id,
            "ci": json.dumps({"whatsapp": "+5511999999999"}),
            "dn": "Test Lead",
            "p": json.dumps({"likes": "coffee"}),
            "lt": False,
            "rl": "elevated",
            "rr": "spamming",
            "am": json.dumps({"utm_source": "google"}),
        },
    )
    result = await db_session.execute(
        text("SELECT risk_level, channel_identifiers->>'whatsapp' FROM leads WHERE id = :id"),
        {"id": lead_id},
    )
    row = result.one()
    assert row[0] == "elevated"
    assert row[1] == "+5511999999999"
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/nicolasamaral/dev/PeSDR && python -m pytest tests/integration/test_migration_0012_leads_extension.py -v`
Expected: FAIL because columns don't exist yet (`column "channel_identifiers" does not exist` or empty result set).

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0012_extend_leads_with_identity_fields.py`:

```python
"""extend leads with long-lived identity fields (FlowEngine FE-01a)

Adds 9 columns to the existing leads table to carry the long-lived Lead
identity used by the FlowEngine: channel routing, display, profile (long-
term memory slot), risk-level state machine for Sentinel, acquisition
metadata for BI attribution.

The earlier draft of the spec named this concept 'User' on a new table.
Renamed to extend 'leads' to avoid collision with the existing P11 'users'
table that holds operators.

Revision ID: 0012_extend_leads_with_identity_fields
Revises: 0011_outbound_messages
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_extend_leads_with_identity_fields"
down_revision = "0011_outbound_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "channel_identifiers",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("leads", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "profile",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("profile_last_updated", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "long_term_memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "risk_level",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'normal'"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("risk_level_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("leads", sa.Column("risk_level_reason", sa.Text(), nullable=True))
    op.add_column(
        "leads",
        sa.Column(
            "acquisition_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.create_check_constraint(
        "ck_leads_risk_level",
        "leads",
        "risk_level IN ('normal', 'elevated', 'banned')",
    )

    op.create_index(
        "ix_leads_tenant_risk_level",
        "leads",
        ["tenant_id", "risk_level"],
        postgresql_where=sa.text("risk_level <> 'normal'"),
    )


def downgrade() -> None:
    op.drop_index("ix_leads_tenant_risk_level", table_name="leads")
    op.drop_constraint("ck_leads_risk_level", "leads", type_="check")
    op.drop_column("leads", "acquisition_metadata")
    op.drop_column("leads", "risk_level_reason")
    op.drop_column("leads", "risk_level_since")
    op.drop_column("leads", "risk_level")
    op.drop_column("leads", "long_term_memory_enabled")
    op.drop_column("leads", "profile_last_updated")
    op.drop_column("leads", "profile")
    op.drop_column("leads", "display_name")
    op.drop_column("leads", "channel_identifiers")
```

- [ ] **Step 4: Apply migration**

Run: `cd /Users/nicolasamaral/dev/PeSDR && alembic upgrade head`
Expected: prints `Running upgrade 0011_outbound_messages -> 0012_extend_leads_with_identity_fields`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0012_leads_extension.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0012_extend_leads_with_identity_fields.py tests/integration/test_migration_0012_leads_extension.py
git commit -m "feat(migration): 0012 extend leads with identity fields"
```

---

## Task 3: Update Lead SQLAlchemy model

**Files:**
- Modify: `src/ai_sdr/models/lead.py`
- Create: `tests/integration/test_lead_model_identity_fields.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_lead_model_identity_fields.py`:

```python
"""Lead model exposes the FlowEngine identity fields."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_lead_model_has_identity_fields(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()

    lead = Lead(
        tenant_id=tenant.id,
        channel_identifiers={"whatsapp": "+5511999999999"},
        display_name="Test",
        profile={"likes": "coffee"},
        long_term_memory_enabled=False,
        risk_level="normal",
        acquisition_metadata={"utm_source": "google"},
    )
    db_session.add(lead)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Lead).where(Lead.id == lead.id))
    ).scalar_one()
    assert fetched.channel_identifiers == {"whatsapp": "+5511999999999"}
    assert fetched.display_name == "Test"
    assert fetched.profile == {"likes": "coffee"}
    assert fetched.long_term_memory_enabled is False
    assert fetched.risk_level == "normal"
    assert fetched.acquisition_metadata == {"utm_source": "google"}
    assert fetched.profile_last_updated is None
    assert fetched.risk_level_since is None
    assert fetched.risk_level_reason is None


@pytest.mark.asyncio
async def test_lead_model_risk_level_typed(db_session: AsyncSession) -> None:
    """RiskLevel literal type rejects unknown values at static checking."""
    from typing import get_args
    from ai_sdr.models.lead import RiskLevel

    assert set(get_args(RiskLevel)) == {"normal", "elevated", "banned"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_lead_model_identity_fields.py -v`
Expected: FAIL because `Lead` model doesn't define the new fields yet (AttributeError or ImportError on `RiskLevel`).

- [ ] **Step 3: Update Lead model**

Edit `src/ai_sdr/models/lead.py`. Replace the file contents with:

```python
"""Lead — the prospect identity. Long-lived across multiple Talks.

A lead is per-tenant (RLS-enforced). It carries an optional `whatsapp_e164`
(unique-per-tenant when set), an optional `external_label` (used by the
simulate CLI's --lead flag and any other dev/admin tooling that wants a
human-readable handle), and a status that gates the worker's behavior:

  - 'pending_assignment' — new lead from inbound; messages queue but no step()
    runs until an operator assigns a treeflow via CLI/REST.
  - 'active' — has an attached talkflow; worker drains inbox via runtime.step().
  - 'unreachable' — provider returned RecipientUnreachable; new inbounds get
    skipped (status_skipped) rather than driving step().

FlowEngine FE-01a extends Lead with long-lived identity fields:

  - channel_identifiers: routing per channel (e.g. WhatsApp e164, Telegram id)
  - display_name: human-friendly label rendered in console + system prompts
  - profile: long-term memory store (V1 disabled; toggled per Lead)
  - risk_level: Sentinel state machine ('normal' / 'elevated' / 'banned')
  - acquisition_metadata: UTM + source for BI attribution

These fields are populated incrementally as the FlowEngine wires them; the
defaults make the columns safe for existing legacy rows.

IMPORTANT: This Lead is NOT the same as the P11 ``users`` table. P11 ``User``
represents an operator (system user); ``Lead`` represents the prospect
being qualified. Never conflate the two.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

LeadStatus = Literal["pending_assignment", "active", "unreachable"]
RiskLevel = Literal["normal", "elevated", "banned"]


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    whatsapp_e164: Mapped[str | None] = mapped_column(Text(), nullable=True)
    external_label: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False, server_default="pending_assignment")
    unreachable_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    # FlowEngine identity (added by migration 0012)
    channel_identifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    display_name: Mapped[str | None] = mapped_column(Text(), nullable=True)
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    profile_last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    long_term_memory_enabled: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, server_default=func.cast("false", Boolean())
    )
    risk_level: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="normal"
    )
    risk_level_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    risk_level_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    acquisition_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_lead_model_identity_fields.py -v`
Expected: 2 PASS

- [ ] **Step 5: Run full lead test suite to verify no regression**

Run: `python -m pytest tests/ -k "lead" -v`
Expected: all existing lead tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/lead.py tests/integration/test_lead_model_identity_fields.py
git commit -m "feat(models): Lead exposes FlowEngine identity fields"
```

---

## Task 4: Migration 0013 — create talks table

**Files:**
- Create: `migrations/versions/0013_create_talks_table.py`
- Create: `tests/integration/test_migration_0013_talks.py`

Talks per spec §3.2. RLS via denormalized `tenant_id` (matches `outbound_messages` pattern). FK to `leads` (CASCADE), `tenants` (CASCADE), `treeflow_versions` (RESTRICT — versions are immutable historic snapshots).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0013_talks.py`:

```python
"""Verifies migration 0013 creates talks table with RLS + indexes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_talks_table_exists_with_columns(db_session: AsyncSession) -> None:
    """All expected columns exist with right types."""
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'talks'
            ORDER BY column_name
            """
        )
    )
    columns = {r[0] for r in result.all()}
    assert columns >= {
        "id", "tenant_id", "lead_id", "treeflow_id", "treeflow_version_id",
        "status", "handling_mode", "created_at", "last_message_at",
        "closed_at", "closed_reason", "closed_by",
        "escalated_at", "escalation_category", "escalation_reason",
        "experiment_id", "experiment_variant",
        "turn_count", "tokens_consumed",
    }


@pytest.mark.asyncio
async def test_talks_rls_isolates_tenants(db_session: AsyncSession) -> None:
    """Rows are invisible to other tenants under RLS."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    for tid in (tenant_a, tenant_b):
        await db_session.execute(
            text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
            {"i": tid, "s": f"t-{tid.hex[:8]}", "n": "t"},
        )

    # Create a treeflow version + lead under tenant_a
    tfv_id = uuid.uuid4()
    lead_a = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1.0', 'x', 'yaml')"
        ),
        {"i": tfv_id, "t": tenant_a},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_a, "t": tenant_a},
    )

    talk_id = uuid.uuid4()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO talks (
                id, tenant_id, lead_id, treeflow_id, treeflow_version_id,
                status, handling_mode, last_message_at
            ) VALUES (
                :id, :tid, :lid, 'tf', :tfv,
                'active', 'ai', now()
            )
            """
        ),
        {"id": talk_id, "tid": tenant_a, "lid": lead_a, "tfv": tfv_id},
    )

    # Switch to tenant_b context — row must be invisible
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_b)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM talks"))
    assert result.scalar_one() == 0

    # Switch back to tenant_a — row must be visible
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_a)},
    )
    result = await db_session.execute(text("SELECT COUNT(*) FROM talks"))
    assert result.scalar_one() == 1
    await db_session.rollback()


@pytest.mark.asyncio
async def test_talks_status_check_constraint(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, :n)"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}", "n": "t"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO talks (tenant_id, lead_id, treeflow_id, "
                "treeflow_version_id, status, handling_mode, last_message_at) "
                "VALUES (:t, :t, 'tf', :t, 'fakestatus', 'ai', now())"
            ),
            {"t": tenant_id},
        )
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0013_talks.py -v`
Expected: FAIL with `relation "talks" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0013_create_talks_table.py`:

```python
"""talks table — conversation session per Lead (FlowEngine FE-01a)

A Talk is a discrete period of agent-lead interaction. A Lead can have
many Talks over time (V1 restricts to one active per tenant). Each Talk
is bound to an immutable TreeFlow version snapshot.

RLS uses denormalized tenant_id (matches outbound_messages pattern).

Revision ID: 0013_create_talks_table
Revises: 0012_extend_leads_with_identity_fields
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013_create_talks_table"
down_revision = "0012_extend_leads_with_identity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "talks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.Text(), nullable=False),
        sa.Column("treeflow_version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("handling_mode", sa.Text(), nullable=False, server_default="ai"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.Column("closed_by", sa.Text(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_category", sa.Text(), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("experiment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_variant", sa.Text(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "tokens_consumed",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["treeflow_version_id"], ["treeflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'requires_review', "
            "'closed_completed', 'closed_inactivity', 'closed_optout', "
            "'closed_banned')",
            name="ck_talks_status",
        ),
        sa.CheckConstraint(
            "handling_mode IN ('ai', 'human', 'auto_with_approval')",
            name="ck_talks_handling_mode",
        ),
        sa.CheckConstraint(
            "closed_by IS NULL OR closed_by IN "
            "('rule', 'optout', 'llm', 'operator', 'sentinel')",
            name="ck_talks_closed_by",
        ),
    )

    op.create_index(
        "ix_talks_tenant_status_last_msg",
        "talks",
        ["tenant_id", "status", sa.text("last_message_at DESC")],
    )
    op.create_index(
        "ix_talks_lead_created",
        "talks",
        ["lead_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_talks_experiment",
        "talks",
        ["experiment_id"],
        postgresql_where=sa.text("experiment_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE talks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE talks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY talks_tenant_isolation ON talks
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS talks_tenant_isolation ON talks")
    op.drop_index("ix_talks_experiment", table_name="talks")
    op.drop_index("ix_talks_lead_created", table_name="talks")
    op.drop_index("ix_talks_tenant_status_last_msg", table_name="talks")
    op.drop_table("talks")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`
Expected: `Running upgrade 0012_extend_leads_with_identity_fields -> 0013_create_talks_table`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0013_talks.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0013_create_talks_table.py tests/integration/test_migration_0013_talks.py
git commit -m "feat(migration): 0013 create talks table with RLS"
```

---

## Task 5: Talk SQLAlchemy model + TalkStatus + HandlingMode

**Files:**
- Create: `src/ai_sdr/models/talk.py`
- Create: `tests/integration/test_talk_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_talk_model.py`:

```python
"""Talk model accepts all fields and exposes typed enums."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import get_args

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import HandlingMode, Talk, TalkStatus
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


def test_status_literal_alias() -> None:
    assert set(get_args(TalkStatus)) == {
        "active", "paused", "requires_review",
        "closed_completed", "closed_inactivity", "closed_optout", "closed_banned",
    }


def test_handling_mode_literal_alias() -> None:
    assert set(get_args(HandlingMode)) == {"ai", "human", "auto_with_approval"}


@pytest.mark.asyncio
async def test_talk_insert_round_trip(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1.0",
        content_hash="x", content_yaml="yaml",
    )
    db_session.add(tfv)
    await db_session.flush()

    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )

    talk = Talk(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
        status="active",
        handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    fetched = (await db_session.execute(select(Talk).where(Talk.id == talk.id))).scalar_one()
    assert fetched.status == "active"
    assert fetched.handling_mode == "ai"
    assert fetched.turn_count == 0
    assert fetched.tokens_consumed == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_talk_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.models.talk'`.

- [ ] **Step 3: Create the model**

Create `src/ai_sdr/models/talk.py`:

```python
"""Talk — a conversation session between agent and Lead.

A Talk is a discrete period of agent-lead interaction. A Lead can have
many Talks over time (V1 restricts to one active per tenant). Each Talk
is bound to an immutable TreeFlow version snapshot for its lifetime.

Lifecycle (status):
  - active                  : pipeline runs, lead is engaged
  - paused                  : reserved (operator pause, V1 unused)
  - requires_review         : escalated to human; handling_mode flips to 'human'
  - closed_completed        : closure rule (success/failure/no_interest) fired
  - closed_inactivity       : exceeded talk_lifecycle.close_after_inactivity
  - closed_optout           : opt-out keyword detected
  - closed_banned           : Sentinel attack verdict

Handling mode controls runtime behavior:
  - ai                      : pipeline generates and sends responses
  - human                   : operator owns the conversation; pipeline only logs
  - auto_with_approval      : pipeline generates; response held in review queue
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

TalkStatus = Literal[
    "active",
    "paused",
    "requires_review",
    "closed_completed",
    "closed_inactivity",
    "closed_optout",
    "closed_banned",
]

HandlingMode = Literal["ai", "human", "auto_with_approval"]


class Talk(Base):
    __tablename__ = "talks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    treeflow_id: Mapped[str] = mapped_column(Text(), nullable=False)
    treeflow_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("treeflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(Text(), nullable=False)
    handling_mode: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="ai"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(Text(), nullable=True)

    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalation_category: Mapped[str | None] = mapped_column(Text(), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    experiment_variant: Mapped[str | None] = mapped_column(Text(), nullable=True)

    turn_count: Mapped[int] = mapped_column(Integer(), nullable=False, server_default="0")
    tokens_consumed: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_talk_model.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talk.py tests/integration/test_talk_model.py
git commit -m "feat(models): Talk SQLAlchemy + TalkStatus/HandlingMode literals"
```

---

## Task 6: Migration 0014 — create talkflow_states table

**Files:**
- Create: `migrations/versions/0014_create_talkflow_states_table.py`
- Create: `tests/integration/test_migration_0014_talkflow_states.py`

Per spec §3.3. 1:1 with Talk (talk_id is the PK). `messages` is the JSONB rolling window (default 15 most recent — runtime enforces, not DB). `tenant_id` is denormalized for RLS performance.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0014_talkflow_states.py`:

```python
"""Verifies migration 0014 creates talkflow_states table."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_talkflow_states_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'talkflow_states'
            ORDER BY column_name
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "talk_id", "tenant_id", "current_node",
        "collected", "extracted_facts", "messages",
        "history_summary", "history_summary_covers_until_turn",
        "active_treatment", "objections_handled", "talkflow_stack",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_talkflow_state_one_to_one_with_talk(db_session: AsyncSession) -> None:
    """A second talkflow_state insert for the same talk_id must fail (PK uniqueness)."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            "treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:i, :t, :l, 'tf', :v, 'active', 'ai', now())"
        ),
        {"i": talk_id, "t": tenant_id, "l": lead_id, "v": tfv_id},
    )

    await db_session.execute(
        text(
            "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
            "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
            "VALUES (:t, :tn, 'saudacao', CAST(:c AS JSONB), CAST(:f AS JSONB), "
            "CAST(:m AS JSONB), CAST(:o AS JSONB), CAST(:s AS JSONB))"
        ),
        {
            "t": talk_id, "tn": tenant_id,
            "c": "{}", "f": "{}", "m": "[]", "o": "[]", "s": "[]",
        },
    )

    with pytest.raises(Exception):
        await db_session.execute(
            text(
                "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
                "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
                "VALUES (:t, :tn, 'other', CAST(:c AS JSONB), CAST(:f AS JSONB), "
                "CAST(:m AS JSONB), CAST(:o AS JSONB), CAST(:s AS JSONB))"
            ),
            {
                "t": talk_id, "tn": tenant_id,
                "c": "{}", "f": "{}", "m": "[]", "o": "[]", "s": "[]",
            },
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_talkflow_state_cascade_on_talk_delete(db_session: AsyncSession) -> None:
    """Deleting the talk removes the talkflow_state."""
    tenant_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    tfv_id = uuid.uuid4()
    talk_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("INSERT INTO leads (id, tenant_id) VALUES (:i, :t)"),
        {"i": lead_id, "t": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO treeflow_versions (id, tenant_id, treeflow_id, version, "
            "content_hash, content_yaml) VALUES (:i, :t, 'tf', '1', 'x', 'y')"
        ),
        {"i": tfv_id, "t": tenant_id},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO talks (id, tenant_id, lead_id, treeflow_id, "
            "treeflow_version_id, status, handling_mode, last_message_at) "
            "VALUES (:i, :t, :l, 'tf', :v, 'active', 'ai', now())"
        ),
        {"i": talk_id, "t": tenant_id, "l": lead_id, "v": tfv_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO talkflow_states (talk_id, tenant_id, current_node, "
            "collected, extracted_facts, messages, objections_handled, talkflow_stack) "
            "VALUES (:t, :tn, 'saudacao', CAST('{}' AS JSONB), CAST('{}' AS JSONB), "
            "CAST('[]' AS JSONB), CAST('[]' AS JSONB), CAST('[]' AS JSONB))"
        ),
        {"t": talk_id, "tn": tenant_id},
    )

    await db_session.execute(text("DELETE FROM talks WHERE id = :i"), {"i": talk_id})
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM talkflow_states WHERE talk_id = :t"),
        {"t": talk_id},
    )
    assert result.scalar_one() == 0
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0014_talkflow_states.py -v`
Expected: FAIL — `relation "talkflow_states" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0014_create_talkflow_states_table.py`:

```python
"""talkflow_states table — runtime state per Talk (FlowEngine FE-01a)

1:1 with Talk (talk_id is PK). Carries the FlowEngine runtime state per
spec §3.3: current node, collected fields, extracted facts, the rolling
window of recent messages, active objection treatment, objection history,
and a slot for the (V2) sub-talk stack.

RLS uses denormalized tenant_id (matches the talks pattern).

Revision ID: 0014_create_talkflow_states_table
Revises: 0013_create_talks_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014_create_talkflow_states_table"
down_revision = "0013_create_talks_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "talkflow_states",
        sa.Column("talk_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("current_node", sa.Text(), nullable=False),
        sa.Column(
            "collected",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "extracted_facts",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "messages",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("history_summary", sa.Text(), nullable=True),
        sa.Column(
            "history_summary_covers_until_turn", sa.Integer(), nullable=True
        ),
        sa.Column("active_treatment", JSONB(), nullable=True),
        sa.Column(
            "objections_handled",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "talkflow_stack",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("talk_id"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_talkflow_states_tenant_updated",
        "talkflow_states",
        ["tenant_id", sa.text("updated_at DESC")],
    )

    op.execute("ALTER TABLE talkflow_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE talkflow_states FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY talkflow_states_tenant_isolation ON talkflow_states
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS talkflow_states_tenant_isolation ON talkflow_states"
    )
    op.drop_index("ix_talkflow_states_tenant_updated", table_name="talkflow_states")
    op.drop_table("talkflow_states")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`
Expected: `Running upgrade 0013_create_talks_table -> 0014_create_talkflow_states_table`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0014_talkflow_states.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0014_create_talkflow_states_table.py tests/integration/test_migration_0014_talkflow_states.py
git commit -m "feat(migration): 0014 create talkflow_states table"
```

---

## Task 7: TalkFlowState SQLAlchemy model

**Files:**
- Create: `src/ai_sdr/models/talkflow_state.py`
- Create: `tests/integration/test_talkflow_state_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_talkflow_state_model.py`:

```python
"""TalkFlowState model wraps the JSONB-heavy state row."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.talkflow_state import TalkFlowState
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion


@pytest.mark.asyncio
async def test_talkflow_state_round_trip(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    talk = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="active", handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    state = TalkFlowState(
        talk_id=talk.id,
        tenant_id=tenant.id,
        current_node="saudacao",
        collected={"segmento": "saas"},
        extracted_facts={"tem_filha": True},
        messages=[
            {"role": "user", "content": "oi", "source": "lead", "turn_index": 1,
             "timestamp": "2026-06-02T10:00:00+00:00"}
        ],
        objections_handled=[],
        talkflow_stack=[],
    )
    db_session.add(state)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(TalkFlowState).where(TalkFlowState.talk_id == talk.id)
        )
    ).scalar_one()
    assert fetched.current_node == "saudacao"
    assert fetched.collected == {"segmento": "saas"}
    assert fetched.extracted_facts == {"tem_filha": True}
    assert fetched.messages[0]["content"] == "oi"
    assert fetched.active_treatment is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_talkflow_state_model.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the model**

Create `src/ai_sdr/models/talkflow_state.py`:

```python
"""TalkFlowState — runtime state per Talk (1:1).

The dynamic, mutable state of an in-flight Talk: where in the TreeFlow we
are (current_node), what's been collected, the rolling message window,
any active objection treatment, and the history of handled objections.

JSONB columns hold structured payloads validated by the Pydantic shapes
in ai_sdr.flowengine.state (loaded at runtime, not enforced at the DB).
Trade-off: schema flexibility for runtime-only validation. Acceptable
because the only writer is the FlowEngine itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class TalkFlowState(Base):
    __tablename__ = "talkflow_states"

    talk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("talks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    current_node: Mapped[str] = mapped_column(Text(), nullable=False)

    collected: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    extracted_facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("{}", JSONB())
    )
    messages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )

    history_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    history_summary_covers_until_turn: Mapped[int | None] = mapped_column(
        Integer(), nullable=True
    )

    active_treatment: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(), nullable=True
    )
    objections_handled: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )
    talkflow_stack: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(), nullable=False, server_default=func.cast("[]", JSONB())
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_talkflow_state_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talkflow_state.py tests/integration/test_talkflow_state_model.py
git commit -m "feat(models): TalkFlowState SQLAlchemy model"
```

---

## Task 8: Migration 0015 — create events table

**Files:**
- Create: `migrations/versions/0015_create_events_table.py`
- Create: `tests/integration/test_migration_0015_events.py`

Per spec §22.2. The events table is the foundation of the event-sourced metrics + BI pipeline (FE-06 wires the emitter). FE-01a creates the schema only — no emitter yet.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0015_events.py`:

```python
"""Verifies migration 0015 creates events table with indexes + RLS."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_events_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'events'
            ORDER BY column_name
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "event_type", "payload",
        "talk_id", "lead_id", "experiment_id", "experiment_variant",
        "occurred_at", "ingested_at",
    }


@pytest.mark.asyncio
async def test_events_table_indexes_present(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'events'"
        )
    )
    idxs = {r[0] for r in result.all()}
    assert "ix_events_tenant_occurred" in idxs
    assert "ix_events_talk" in idxs
    assert "ix_events_type_occurred" in idxs


@pytest.mark.asyncio
async def test_events_insert_round_trip(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )

    await db_session.execute(
        text(
            "INSERT INTO events (tenant_id, event_type, payload, occurred_at) "
            "VALUES (:t, 'turn.completed', CAST(:p AS JSONB), :o)"
        ),
        {
            "t": tenant_id,
            "p": json.dumps({"talk_id": str(uuid.uuid4())}),
            "o": datetime.now(timezone.utc),
        },
    )

    result = await db_session.execute(
        text("SELECT event_type FROM events WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    assert result.scalar_one() == "turn.completed"
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0015_events.py -v`
Expected: FAIL — `relation "events" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0015_create_events_table.py`:

```python
"""events table — event-sourced audit + BI feed (FlowEngine FE-01a)

Per spec §22: the canonical event log. Emitters are added in FE-06; this
migration only lays down the schema so emitter wiring has somewhere to
write.

Revision ID: 0015_create_events_table
Revises: 0014_create_talkflow_states_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0015_create_events_table"
down_revision = "0014_create_talkflow_states_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_variant", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index(
        "ix_events_tenant_occurred",
        "events",
        ["tenant_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_events_talk",
        "events",
        ["talk_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("talk_id IS NOT NULL"),
    )
    op.create_index(
        "ix_events_type_occurred",
        "events",
        ["event_type", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_events_experiment",
        "events",
        ["experiment_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("experiment_id IS NOT NULL"),
    )

    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY events_tenant_isolation ON events
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS events_tenant_isolation ON events")
    op.drop_index("ix_events_experiment", table_name="events")
    op.drop_index("ix_events_type_occurred", table_name="events")
    op.drop_index("ix_events_talk", table_name="events")
    op.drop_index("ix_events_tenant_occurred", table_name="events")
    op.drop_table("events")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`
Expected: `Running upgrade 0014_create_talkflow_states_table -> 0015_create_events_table`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0015_events.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0015_create_events_table.py tests/integration/test_migration_0015_events.py
git commit -m "feat(migration): 0015 create events table"
```

---

## Task 9: Event SQLAlchemy model

**Files:**
- Create: `src/ai_sdr/models/event.py`
- Create: `tests/integration/test_event_model.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_event_model.py`:

```python
"""Event model wraps the events row."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.event import Event
from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_event_round_trip(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    e = Event(
        tenant_id=tenant.id,
        event_type="turn.completed",
        payload={"talk_id": "x", "tokens_total_cost_usd": "0.02"},
        occurred_at=datetime.now(timezone.utc),
    )
    db_session.add(e)
    await db_session.flush()

    fetched = (await db_session.execute(select(Event).where(Event.id == e.id))).scalar_one()
    assert fetched.event_type == "turn.completed"
    assert fetched.payload["tokens_total_cost_usd"] == "0.02"
    assert fetched.ingested_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_event_model.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the model**

Create `src/ai_sdr/models/event.py`:

```python
"""Event — canonical row of the event-sourced log.

Each FlowEngine state change emits an Event. Subscribers (materialized
views, notification fan-out, BI sinks) consume via Postgres LISTEN/NOTIFY
or polling. See spec §22.

Schema laid down in FE-01a; emitter wiring lives in FE-06.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)

    talk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    experiment_variant: Mapped[str | None] = mapped_column(Text(), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_event_model.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/event.py tests/integration/test_event_model.py
git commit -m "feat(models): Event SQLAlchemy model"
```

---

## Task 10: Migration 0016 — experiments table (slot)

**Files:**
- Create: `migrations/versions/0016_create_experiments_table.py`
- Create: `tests/integration/test_migration_0016_experiments.py`

Per spec §25.1. Tables exist now so FE-07 can wire assignment + analytics. No model class in FE-01a — added in FE-07.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0016_experiments.py`:

```python
"""Verifies migration 0016 creates experiments table (reserved slot)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_experiments_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'experiments' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "name", "key", "variants", "status",
        "eligibility_rules", "started_at", "expected_end",
        "target_sample_size", "primary_success_metric", "secondary_metrics",
        "exclusivity", "priority", "on_conclusion_behavior",
        "winner", "statistical_confidence", "analysis_notes", "created_at",
    }


@pytest.mark.asyncio
async def test_experiments_insert_round_trip(db_session: AsyncSession) -> None:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO experiments (tenant_id, name, key, variants, status, "
            "eligibility_rules, target_sample_size, primary_success_metric, "
            "secondary_metrics, exclusivity, priority, on_conclusion_behavior) "
            "VALUES (:t, 'exp1', 'exp1_key', CAST(:v AS JSONB), 'draft', "
            "CAST(:e AS JSONB), 100, 'conversion_rate', CAST(:s AS JSONB), "
            "'exclusive', 0, 'preserve_running_talks')"
        ),
        {
            "t": tenant_id,
            "v": json.dumps({"A": {"treeflow_version_id": str(uuid.uuid4()), "split": 0.5}}),
            "e": json.dumps([]),
            "s": json.dumps([]),
        },
    )
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM experiments WHERE tenant_id = :t"),
        {"t": tenant_id},
    )
    assert result.scalar_one() == 1
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0016_experiments.py -v`
Expected: FAIL — `relation "experiments" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0016_create_experiments_table.py`:

```python
"""experiments table — A/B test definitions (FlowEngine FE-01a, reserved slot)

Per spec §25. Schema is laid down now so FE-07 can implement assignment
and analytics. Empty at v1 launch; populated when first experiment is
created.

Revision ID: 0016_create_experiments_table
Revises: 0015_create_events_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016_create_experiments_table"
down_revision = "0015_create_events_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("variants", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column(
            "eligibility_rules",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_sample_size", sa.Integer(), nullable=False),
        sa.Column("primary_success_metric", sa.Text(), nullable=False),
        sa.Column(
            "secondary_metrics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("exclusivity", sa.Text(), nullable=False, server_default="exclusive"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "on_conclusion_behavior",
            sa.Text(),
            nullable=False,
            server_default="preserve_running_talks",
        ),
        sa.Column("winner", sa.Text(), nullable=True),
        sa.Column("statistical_confidence", sa.Float(), nullable=True),
        sa.Column("analysis_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_experiments_tenant_key"),
        sa.CheckConstraint(
            "status IN ('draft', 'running', 'paused', 'concluded')",
            name="ck_experiments_status",
        ),
        sa.CheckConstraint(
            "exclusivity IN ('exclusive', 'orthogonal')",
            name="ck_experiments_exclusivity",
        ),
        sa.CheckConstraint(
            "on_conclusion_behavior IN ('preserve_running_talks', 'migrate_to_winner')",
            name="ck_experiments_on_conclusion",
        ),
    )

    op.create_index(
        "ix_experiments_tenant_status",
        "experiments",
        ["tenant_id", "status"],
    )

    op.execute("ALTER TABLE experiments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiments FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY experiments_tenant_isolation ON experiments
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS experiments_tenant_isolation ON experiments")
    op.drop_index("ix_experiments_tenant_status", table_name="experiments")
    op.drop_table("experiments")
```

- [ ] **Step 4: Apply migration + run test**

Run:
```
alembic upgrade head
python -m pytest tests/integration/test_migration_0016_experiments.py -v
```
Expected: migration applies; 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0016_create_experiments_table.py tests/integration/test_migration_0016_experiments.py
git commit -m "feat(migration): 0016 create experiments table (reserved slot)"
```

---

## Task 11: Migration 0017 — response_reviews table (slot)

**Files:**
- Create: `migrations/versions/0017_create_response_reviews_table.py`
- Create: `tests/integration/test_migration_0017_response_reviews.py`

Per spec §24.1. Reserved for HITL approval workflow (FE-07 wires runtime).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0017_response_reviews.py`:

```python
"""Verifies migration 0017 creates response_reviews table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_response_reviews_table_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'response_reviews' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "talk_id", "turn_index",
        "correction_iteration", "parent_review_id",
        "original_response", "original_turn_decision", "original_system_prompt_snapshot",
        "status", "operator_id", "decision_at",
        "edited_response", "edit_reason",
        "rejection_reason", "improvement_category",
        "final_response_sent", "created_at", "expires_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0017_response_reviews.py -v`
Expected: FAIL — `relation "response_reviews" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0017_create_response_reviews_table.py`:

```python
"""response_reviews table — HITL approval queue (FlowEngine FE-01a, reserved slot)

Per spec §24.1. Reserved terreno: tables exist, runtime activation in FE-07.

Revision ID: 0017_create_response_reviews_table
Revises: 0016_create_experiments_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017_create_response_reviews_table"
down_revision = "0016_create_experiments_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column(
            "correction_iteration", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("parent_review_id", UUID(as_uuid=True), nullable=True),
        sa.Column("original_response", sa.Text(), nullable=False),
        sa.Column("original_turn_decision", JSONB(), nullable=False),
        sa.Column(
            "original_system_prompt_snapshot", sa.Text(), nullable=True
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("operator_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_response", sa.Text(), nullable=True),
        sa.Column("edit_reason", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("improvement_category", sa.Text(), nullable=True),
        sa.Column("final_response_sent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_review_id"], ["response_reviews.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'edited', 'rejected', 'expired', 'auto_approved')",
            name="ck_response_reviews_status",
        ),
        sa.CheckConstraint(
            "improvement_category IS NULL OR improvement_category IN "
            "('tone', 'factual', 'scope', 'premature_transition', "
            "'missed_signal', 'incomplete', 'other')",
            name="ck_response_reviews_improvement_category",
        ),
    )

    op.create_index(
        "ix_response_reviews_tenant_status_created",
        "response_reviews",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_response_reviews_talk",
        "response_reviews",
        ["talk_id", "turn_index"],
    )

    op.execute("ALTER TABLE response_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE response_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY response_reviews_tenant_isolation ON response_reviews
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS response_reviews_tenant_isolation ON response_reviews"
    )
    op.drop_index("ix_response_reviews_talk", table_name="response_reviews")
    op.drop_index(
        "ix_response_reviews_tenant_status_created", table_name="response_reviews"
    )
    op.drop_table("response_reviews")
```

- [ ] **Step 4: Apply migration + run test**

Run:
```
alembic upgrade head
python -m pytest tests/integration/test_migration_0017_response_reviews.py -v
```
Expected: migration applies; PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0017_create_response_reviews_table.py tests/integration/test_migration_0017_response_reviews.py
git commit -m "feat(migration): 0017 create response_reviews table (reserved slot)"
```

---

## Task 12: Migration 0018 — sentinel_reviews table (slot)

**Files:**
- Create: `migrations/versions/0018_create_sentinel_reviews_table.py`
- Create: `tests/integration/test_migration_0018_sentinel_reviews.py`

Per spec §8.5. Records each Sentinel invocation: triggered_by, verdict, reasoning, transition of risk_level.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0018_sentinel_reviews.py`:

```python
"""Verifies migration 0018 creates sentinel_reviews table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_sentinel_reviews_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sentinel_reviews' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "lead_id", "talk_id", "inbound_message_id",
        "triggered_by", "classification", "reasoning", "confidence",
        "risk_level_before", "risk_level_after",
        "heuristic_matches", "created_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0018_sentinel_reviews.py -v`
Expected: FAIL — `relation "sentinel_reviews" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0018_create_sentinel_reviews_table.py`:

```python
"""sentinel_reviews table — Sentinel audit (FlowEngine FE-01a, reserved slot)

Per spec §8.5. Records each Sentinel invocation (heuristic-triggered or
elevated-mode). FE-04 implements the runtime that writes here.

Revision ID: 0018_create_sentinel_reviews_table
Revises: 0017_create_response_reviews_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0018_create_sentinel_reviews_table"
down_revision = "0017_create_response_reviews_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sentinel_reviews",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level_before", sa.Text(), nullable=False),
        sa.Column("risk_level_after", sa.Text(), nullable=False),
        sa.Column(
            "heuristic_matches",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["inbound_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "classification IN ('safe', 'suspicious', 'attack')",
            name="ck_sentinel_reviews_classification",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('heuristic', 'elevated_mode', 'llm_self_flag')",
            name="ck_sentinel_reviews_triggered_by",
        ),
    )

    op.create_index(
        "ix_sentinel_reviews_lead_created",
        "sentinel_reviews",
        ["lead_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_sentinel_reviews_tenant_classification",
        "sentinel_reviews",
        ["tenant_id", "classification", sa.text("created_at DESC")],
    )

    op.execute("ALTER TABLE sentinel_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sentinel_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY sentinel_reviews_tenant_isolation ON sentinel_reviews
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS sentinel_reviews_tenant_isolation ON sentinel_reviews"
    )
    op.drop_index(
        "ix_sentinel_reviews_tenant_classification", table_name="sentinel_reviews"
    )
    op.drop_index("ix_sentinel_reviews_lead_created", table_name="sentinel_reviews")
    op.drop_table("sentinel_reviews")
```

- [ ] **Step 4: Apply migration + run test**

Run:
```
alembic upgrade head
python -m pytest tests/integration/test_migration_0018_sentinel_reviews.py -v
```
Expected: migration applies; PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0018_create_sentinel_reviews_table.py tests/integration/test_migration_0018_sentinel_reviews.py
git commit -m "feat(migration): 0018 create sentinel_reviews table (reserved slot)"
```

---

## Task 13: Migration 0019 — adapter_calls table (slot)

**Files:**
- Create: `migrations/versions/0019_create_adapter_calls_table.py`
- Create: `tests/integration/test_migration_0019_adapter_calls.py`

Per spec §13 (adapter framework, audit row per call). FE-05 implements adapters that write here.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0019_adapter_calls.py`:

```python
"""Verifies migration 0019 creates adapter_calls table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_adapter_calls_columns(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'adapter_calls' ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "talk_id", "lead_id",
        "adapter_category", "adapter_provider", "operation",
        "args", "result", "status", "error_detail",
        "latency_ms", "idempotency_key",
        "started_at", "completed_at", "created_at",
    }


@pytest.mark.asyncio
async def test_adapter_calls_idempotency_key_unique(db_session: AsyncSession) -> None:
    """Same idempotency_key in same tenant cannot be inserted twice."""
    import uuid
    from datetime import datetime, timezone

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, slug, display_name) VALUES (:i, :s, 't')"),
        {"i": tenant_id, "s": f"t-{tenant_id.hex[:8]}"},
    )
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    insert_sql = text(
        "INSERT INTO adapter_calls (tenant_id, adapter_category, "
        "adapter_provider, operation, args, status, idempotency_key, started_at) "
        "VALUES (:t, 'crm', 'kommo', 'create_lead', CAST('{}' AS JSONB), 'ok', "
        ":k, :s)"
    )
    await db_session.execute(
        insert_sql,
        {"t": tenant_id, "k": "abc", "s": datetime.now(timezone.utc)},
    )
    with pytest.raises(Exception):
        await db_session.execute(
            insert_sql,
            {"t": tenant_id, "k": "abc", "s": datetime.now(timezone.utc)},
        )
    await db_session.rollback()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0019_adapter_calls.py -v`
Expected: FAIL — `relation "adapter_calls" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0019_create_adapter_calls_table.py`:

```python
"""adapter_calls table — adapter audit log (FlowEngine FE-01a, reserved slot)

Per spec §13. Each call to a generalized adapter (CRM, calendar,
notification, analytics, storage, voice) writes a row here for audit,
retry tracking, and BI cost reporting. FE-05 wires the dispatch.

Idempotency: (tenant_id, idempotency_key) is unique to prevent duplicate
side effects when worker retries.

Revision ID: 0019_create_adapter_calls_table
Revises: 0018_create_sentinel_reviews_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019_create_adapter_calls_table"
down_revision = "0018_create_sentinel_reviews_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adapter_calls",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talk_id", UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("adapter_category", sa.Text(), nullable=False),
        sa.Column("adapter_provider", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column(
            "args",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["talk_id"], ["talks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_adapter_calls_tenant_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'failed', 'pending', 'cancelled')",
            name="ck_adapter_calls_status",
        ),
    )

    op.create_index(
        "ix_adapter_calls_tenant_started",
        "adapter_calls",
        ["tenant_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_adapter_calls_talk",
        "adapter_calls",
        ["talk_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("talk_id IS NOT NULL"),
    )
    op.create_index(
        "ix_adapter_calls_failed",
        "adapter_calls",
        ["tenant_id", sa.text("started_at DESC")],
        postgresql_where=sa.text("status = 'failed'"),
    )

    op.execute("ALTER TABLE adapter_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE adapter_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY adapter_calls_tenant_isolation ON adapter_calls
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS adapter_calls_tenant_isolation ON adapter_calls")
    op.drop_index("ix_adapter_calls_failed", table_name="adapter_calls")
    op.drop_index("ix_adapter_calls_talk", table_name="adapter_calls")
    op.drop_index("ix_adapter_calls_tenant_started", table_name="adapter_calls")
    op.drop_table("adapter_calls")
```

- [ ] **Step 4: Apply migration + run test**

Run:
```
alembic upgrade head
python -m pytest tests/integration/test_migration_0019_adapter_calls.py -v
```
Expected: migration applies; 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0019_create_adapter_calls_table.py tests/integration/test_migration_0019_adapter_calls.py
git commit -m "feat(migration): 0019 create adapter_calls table (reserved slot)"
```

---

## Task 14: Migration 0020 — treeflow_improvement_suggestions table (slot)

**Files:**
- Create: `migrations/versions/0020_create_treeflow_improvement_suggestions_table.py`
- Create: `tests/integration/test_migration_0020_treeflow_improvement_suggestions.py`

Per spec §24.4. Operator feedback loop (V2). Schema in place now so FE-07 batch job can write rows.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0020_treeflow_improvement_suggestions.py`:

```python
"""Verifies migration 0020 creates treeflow_improvement_suggestions table."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_treeflow_improvement_suggestions_columns(
    db_session: AsyncSession,
) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'treeflow_improvement_suggestions' "
            "ORDER BY column_name"
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols >= {
        "id", "tenant_id", "treeflow_id", "target_node_id",
        "pattern_summary", "sample_count", "sample_review_ids",
        "suggested_change", "suggested_change_natural_language",
        "confidence", "status", "operator_decision_at", "created_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0020_treeflow_improvement_suggestions.py -v`
Expected: FAIL — `relation "treeflow_improvement_suggestions" does not exist`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0020_create_treeflow_improvement_suggestions_table.py`:

```python
"""treeflow_improvement_suggestions table (FlowEngine FE-01a, reserved slot)

Per spec §24.4. V2 feedback loop: weekly batch job analyzes rejected
response_reviews and proposes TreeFlow changes. Operator reviews via API.

Revision ID: 0020_create_treeflow_improvement_suggestions_table
Revises: 0019_create_adapter_calls_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0020_create_treeflow_improvement_suggestions_table"
down_revision = "0019_create_adapter_calls_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "treeflow_improvement_suggestions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("treeflow_id", sa.Text(), nullable=False),
        sa.Column("target_node_id", sa.Text(), nullable=True),
        sa.Column("pattern_summary", sa.Text(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "sample_review_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("suggested_change", JSONB(), nullable=False),
        sa.Column("suggested_change_natural_language", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="pending_review"
        ),
        sa.Column("operator_decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending_review', 'accepted', 'rejected', 'expired')",
            name="ck_tfis_status",
        ),
    )

    op.create_index(
        "ix_tfis_tenant_status",
        "treeflow_improvement_suggestions",
        ["tenant_id", "status", sa.text("created_at DESC")],
    )

    op.execute(
        "ALTER TABLE treeflow_improvement_suggestions ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE treeflow_improvement_suggestions FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tfis_tenant_isolation ON treeflow_improvement_suggestions
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tfis_tenant_isolation ON treeflow_improvement_suggestions"
    )
    op.drop_index("ix_tfis_tenant_status", table_name="treeflow_improvement_suggestions")
    op.drop_table("treeflow_improvement_suggestions")
```

- [ ] **Step 4: Apply migration + run test**

Run:
```
alembic upgrade head
python -m pytest tests/integration/test_migration_0020_treeflow_improvement_suggestions.py -v
```
Expected: migration applies; PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0020_create_treeflow_improvement_suggestions_table.py tests/integration/test_migration_0020_treeflow_improvement_suggestions.py
git commit -m "feat(migration): 0020 create treeflow_improvement_suggestions table"
```

---

## Task 15: Migration 0021 — extend outbound_messages with media fields

**Files:**
- Create: `migrations/versions/0021_extend_outbound_messages_with_media.py`
- Modify: `src/ai_sdr/models/outbound_message.py`
- Create: `tests/integration/test_migration_0021_outbound_media.py`

Add audio/voice fields per spec §13.4 (VoiceAdapter writes here in FE-05). All nullable to preserve existing rows.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0021_outbound_media.py`:

```python
"""Verifies migration 0021 added media fields to outbound_messages."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_outbound_messages_has_media_fields(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'outbound_messages'
              AND column_name IN (
                  'media_type', 'media_storage_key', 'audio_url',
                  'audio_duration_ms', 'synthesis_voice_id',
                  'voice_emotion'
              )
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols == {
        "media_type", "media_storage_key", "audio_url",
        "audio_duration_ms", "synthesis_voice_id", "voice_emotion",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0021_outbound_media.py -v`
Expected: FAIL — `assert cols == {...}` (set is empty).

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0021_extend_outbound_messages_with_media.py`:

```python
"""extend outbound_messages with voice/media fields (FlowEngine FE-01a)

Per spec §13.4 (VoiceAdapter). Existing rows keep defaults (media_type='text').

Revision ID: 0021_extend_outbound_messages_with_media
Revises: 0020_create_treeflow_improvement_suggestions_table
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_extend_outbound_messages_with_media"
down_revision = "0020_create_treeflow_improvement_suggestions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outbound_messages",
        sa.Column("media_type", sa.Text(), nullable=False, server_default="text"),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("media_storage_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "outbound_messages", sa.Column("audio_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "outbound_messages",
        sa.Column("audio_duration_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("synthesis_voice_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "outbound_messages",
        sa.Column("voice_emotion", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_outbound_media_type",
        "outbound_messages",
        "media_type IN ('text', 'audio', 'image', 'video')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_outbound_media_type", "outbound_messages", type_="check")
    op.drop_column("outbound_messages", "voice_emotion")
    op.drop_column("outbound_messages", "synthesis_voice_id")
    op.drop_column("outbound_messages", "audio_duration_ms")
    op.drop_column("outbound_messages", "audio_url")
    op.drop_column("outbound_messages", "media_storage_key")
    op.drop_column("outbound_messages", "media_type")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`
Expected: `Running upgrade 0020_... -> 0021_extend_outbound_messages_with_media`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0021_outbound_media.py -v`
Expected: PASS

- [ ] **Step 6: Update OutboundMessage model**

Edit `src/ai_sdr/models/outbound_message.py`. First, ensure the SQLAlchemy import line includes `Integer`. Find:

```python
from sqlalchemy import DateTime, ForeignKey, Text, func
```

Replace with:

```python
from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
```

Then, inside the `OutboundMessage` class, just before the `sent_at` line, insert these 6 columns:

```python
    media_type: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="text"
    )
    media_storage_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audio_duration_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    synthesis_voice_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    voice_emotion: Mapped[str | None] = mapped_column(Text(), nullable=True)
```

- [ ] **Step 7: Verify model still imports + outbound tests pass**

Run: `python -m pytest tests/ -k "outbound" -v`
Expected: all PASS (no existing test should regress).

- [ ] **Step 8: Commit**

```bash
git add migrations/versions/0021_extend_outbound_messages_with_media.py src/ai_sdr/models/outbound_message.py tests/integration/test_migration_0021_outbound_media.py
git commit -m "feat(migration): 0021 extend outbound_messages with media fields"
```

---

## Task 16: Migration 0022 — extend inbound_messages with media fields

**Files:**
- Create: `migrations/versions/0022_extend_inbound_messages_with_media.py`
- Modify: `src/ai_sdr/models/inbound_message.py`
- Create: `tests/integration/test_migration_0022_inbound_media.py`

Symmetric to outbound — inbound voice messages need transcription + audio reference fields.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migration_0022_inbound_media.py`:

```python
"""Verifies migration 0022 added media fields to inbound_messages."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_inbound_messages_has_media_fields(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'inbound_messages'
              AND column_name IN (
                  'media_type', 'media_storage_key', 'audio_url',
                  'transcription', 'transcription_confidence',
                  'transcription_provider'
              )
            """
        )
    )
    cols = {r[0] for r in result.all()}
    assert cols == {
        "media_type", "media_storage_key", "audio_url",
        "transcription", "transcription_confidence", "transcription_provider",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_migration_0022_inbound_media.py -v`
Expected: FAIL.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0022_extend_inbound_messages_with_media.py`:

```python
"""extend inbound_messages with voice/media fields (FlowEngine FE-01a)

Per spec §13.4. Existing rows keep defaults (media_type='text').

Revision ID: 0022_extend_inbound_messages_with_media
Revises: 0021_extend_outbound_messages_with_media
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_extend_inbound_messages_with_media"
down_revision = "0021_extend_outbound_messages_with_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbound_messages",
        sa.Column("media_type", sa.Text(), nullable=False, server_default="text"),
    )
    op.add_column(
        "inbound_messages",
        sa.Column("media_storage_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "inbound_messages", sa.Column("audio_url", sa.Text(), nullable=True)
    )
    op.add_column(
        "inbound_messages", sa.Column("transcription", sa.Text(), nullable=True)
    )
    op.add_column(
        "inbound_messages",
        sa.Column("transcription_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "inbound_messages",
        sa.Column("transcription_provider", sa.Text(), nullable=True),
    )

    op.create_check_constraint(
        "ck_inbound_media_type",
        "inbound_messages",
        "media_type IN ('text', 'audio', 'image', 'video', 'document')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_inbound_media_type", "inbound_messages", type_="check")
    op.drop_column("inbound_messages", "transcription_provider")
    op.drop_column("inbound_messages", "transcription_confidence")
    op.drop_column("inbound_messages", "transcription")
    op.drop_column("inbound_messages", "audio_url")
    op.drop_column("inbound_messages", "media_storage_key")
    op.drop_column("inbound_messages", "media_type")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_migration_0022_inbound_media.py -v`
Expected: PASS

- [ ] **Step 6: Update InboundMessageRow model**

Edit `src/ai_sdr/models/inbound_message.py`. First, ensure the SQLAlchemy import line includes `Float`. Find:

```python
from sqlalchemy import DateTime, ForeignKey, Text, func
```

Replace with:

```python
from sqlalchemy import DateTime, Float, ForeignKey, Text, func
```

Then, inside the `InboundMessageRow` class, after the existing fields, add:

```python
    media_type: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="text"
    )
    media_storage_key: Mapped[str | None] = mapped_column(Text(), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    transcription: Mapped[str | None] = mapped_column(Text(), nullable=True)
    transcription_confidence: Mapped[float | None] = mapped_column(Float(), nullable=True)
    transcription_provider: Mapped[str | None] = mapped_column(Text(), nullable=True)
```

- [ ] **Step 7: Run regression**

Run: `python -m pytest tests/ -k "inbound" -v`
Expected: existing inbound tests still pass.

- [ ] **Step 8: Commit**

```bash
git add migrations/versions/0022_extend_inbound_messages_with_media.py src/ai_sdr/models/inbound_message.py tests/integration/test_migration_0022_inbound_media.py
git commit -m "feat(migration): 0022 extend inbound_messages with media fields"
```

---

## Task 17: Add architecture_version to Tenant

**Files:**
- Create: `migrations/versions/0023_add_tenant_architecture_version.py`
- Modify: `src/ai_sdr/models/tenant.py`
- Create: `tests/integration/test_tenant_architecture_version.py`

Per spec §21.2. Feature flag that lets `process_lead_inbox` route to v1 (LangGraph, default) or v2 (FlowEngine, FE-01b activates). Default `1` keeps existing behavior on migration. This is migration 0023; migration 0024 (drop LangGraph tables) is reserved for FE-02.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_tenant_architecture_version.py`:

```python
"""Tenant carries an architecture_version feature flag."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.tenant import Tenant


@pytest.mark.asyncio
async def test_tenant_architecture_version_defaults_to_1(
    db_session: AsyncSession,
) -> None:
    t = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(t)
    await db_session.flush()
    fetched = (
        await db_session.execute(select(Tenant).where(Tenant.id == t.id))
    ).scalar_one()
    assert fetched.architecture_version == 1


@pytest.mark.asyncio
async def test_tenant_architecture_version_can_be_set_to_2(
    db_session: AsyncSession,
) -> None:
    t = Tenant(
        slug=f"t-{uuid.uuid4().hex[:8]}",
        display_name="t",
        architecture_version=2,
    )
    db_session.add(t)
    await db_session.flush()
    assert t.architecture_version == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_tenant_architecture_version.py -v`
Expected: FAIL — `AttributeError: 'Tenant' object has no attribute 'architecture_version'`.

- [ ] **Step 3: Create the migration**

Create `migrations/versions/0023_add_tenant_architecture_version.py`:

```python
"""add tenant.architecture_version (FlowEngine FE-01a)

Per spec §21.2. Feature flag routing process_lead_inbox between v1
(LangGraph) and v2 (FlowEngine pipeline). Default 1 keeps existing
behavior; FE-01b sets specific tenants to 2 to activate the new pipeline.

Revision ID: 0023_add_tenant_architecture_version
Revises: 0022_extend_inbound_messages_with_media
Create Date: 2026-06-02 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_add_tenant_architecture_version"
down_revision = "0022_extend_inbound_messages_with_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "architecture_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "ck_tenants_architecture_version",
        "tenants",
        "architecture_version IN (1, 2)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tenants_architecture_version", "tenants", type_="check")
    op.drop_column("tenants", "architecture_version")
```

- [ ] **Step 4: Apply migration**

Run: `alembic upgrade head`

- [ ] **Step 5: Update Tenant model**

Edit `src/ai_sdr/models/tenant.py`. Add `Integer` to imports and add the column:

```python
"""Tenant model (multi-tenant root)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    architecture_version: Mapped[int] = mapped_column(
        Integer(), nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_tenant_architecture_version.py -v`
Expected: 2 PASS.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0023_add_tenant_architecture_version.py src/ai_sdr/models/tenant.py tests/integration/test_tenant_architecture_version.py
git commit -m "feat(models): tenant.architecture_version feature flag (default 1)"
```

---

## Task 18: Pydantic state schemas (Message, ActiveTreatment, ObjectionHistoryEntry, StackFrame)

**Files:**
- Create: `src/ai_sdr/flowengine/state.py`
- Create: `tests/unit/test_flowengine_state_schema.py`

Per spec §3.3 + §6.2 (active_treatment usage in fresh prompt layer). These shape the JSONB payloads in `talkflow_states`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_flowengine_state_schema.py`:

```python
"""Pydantic shapes for TalkFlowState JSONB payloads."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.state import (
    ActiveTreatment,
    Message,
    ObjectionHistoryEntry,
    StackFrame,
)


def test_message_round_trip() -> None:
    m = Message(
        role="user",
        content="oi",
        source="lead",
        turn_index=1,
        timestamp=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
    )
    dumped = m.model_dump(mode="json")
    assert dumped["role"] == "user"
    assert dumped["source"] == "lead"
    assert dumped["media_type"] == "text"
    reloaded = Message.model_validate(dumped)
    assert reloaded == m


def test_message_audio_with_storage_key() -> None:
    m = Message(
        role="user",
        content="(audio: ...)",
        source="lead",
        turn_index=1,
        timestamp=datetime(2026, 6, 2, 10, tzinfo=timezone.utc),
        media_type="audio",
        media_storage_key="s3://bucket/key.ogg",
    )
    assert m.media_storage_key == "s3://bucket/key.ogg"


def test_message_role_validates() -> None:
    with pytest.raises(ValidationError):
        Message(
            role="unknown",  # type: ignore[arg-type]
            content="x",
            source="lead",
            turn_index=1,
            timestamp=datetime.now(timezone.utc),
        )


def test_active_treatment_round_trip() -> None:
    at = ActiveTreatment(
        objection_id="preco",
        started_at_turn=3,
        current_treatment_turn=2,
        max_treatment_turns=3,
        resolution_criteria="lead aceitou parcelamento",
        treatment_history=["argued ROI"],
    )
    reloaded = ActiveTreatment.model_validate(at.model_dump(mode="json"))
    assert reloaded.objection_id == "preco"
    assert reloaded.current_treatment_turn == 2


def test_objection_history_entry_validates_resolution() -> None:
    e = ObjectionHistoryEntry(
        objection_id="preco",
        detected_at_turn=2,
        resolved_at_turn=5,
        resolution="accepted",
    )
    assert e.resolution == "accepted"


def test_stack_frame_default_marker() -> None:
    """V1 always has a single placeholder frame for forward compat."""
    f = StackFrame(node_id="saudacao", entered_at_turn=1)
    assert f.return_to_node_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_flowengine_state_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_sdr.flowengine.state'`.

- [ ] **Step 3: Create the module**

Create `src/ai_sdr/flowengine/state.py`:

```python
"""Pydantic shapes for TalkFlowState JSONB payloads.

These types validate the structured fields that the FlowEngine stores in
``talkflow_states`` (messages list, active_treatment, objections_handled,
talkflow_stack). The DB column is JSONB; Pydantic enforces structure at
the application boundary.

Stable for FE-01a; runtime that USES these lives in FE-01b and later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MessageRole = Literal["user", "assistant"]
MessageSource = Literal["lead", "agent", "operator"]
MediaType = Literal["text", "audio", "image", "video"]
ObjectionResolution = Literal["accepted", "deferred", "exhausted"]


class Message(BaseModel):
    """One entry in TalkFlowState.messages rolling window."""

    role: MessageRole
    content: str
    source: MessageSource
    media_type: MediaType = "text"
    media_storage_key: str | None = None
    turn_index: int = Field(ge=1)
    timestamp: datetime


class ActiveTreatment(BaseModel):
    """Active objection treatment state, set when a treatment is in progress."""

    objection_id: str
    started_at_turn: int = Field(ge=1)
    current_treatment_turn: int = Field(ge=1)
    max_treatment_turns: int = Field(ge=1)
    resolution_criteria: str = Field(min_length=1)
    treatment_history: list[str] = Field(default_factory=list)


class ObjectionHistoryEntry(BaseModel):
    """Record of an objection that was previously detected (resolved or not)."""

    objection_id: str
    detected_at_turn: int = Field(ge=1)
    resolved_at_turn: int | None = None
    resolution: ObjectionResolution | None = None


class StackFrame(BaseModel):
    """Sub-talk frame (V2 subflow capability). V1 always [single_frame]."""

    node_id: str
    entered_at_turn: int = Field(ge=1)
    return_to_node_id: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_flowengine_state_schema.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/state.py tests/unit/test_flowengine_state_schema.py
git commit -m "feat(flowengine): Pydantic shapes for TalkFlowState payloads"
```

---

## Task 19: Pydantic decision schemas (TurnDecision, HumanEscalation, JudgeVerdict)

**Files:**
- Create: `src/ai_sdr/flowengine/decision.py`
- Create: `tests/unit/test_flowengine_decision_schema.py`

Per spec §5. This is THE structured output of the main LLM each turn.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_flowengine_decision_schema.py`:

```python
"""TurnDecision is the single structured output of the main LLM call."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_sdr.flowengine.decision import (
    HumanEscalation,
    JudgeVerdict,
    TurnDecision,
)


def test_minimal_valid_turn_decision() -> None:
    d = TurnDecision(
        response_text="oi! qual seu segmento?",
        collected_fields={"segmento": "saas"},
        reasoning="greeted lead and extracted segment",
    )
    assert d.response_text == "oi! qual seu segmento?"
    assert d.collected_fields == {"segmento": "saas"}
    assert d.intends_to_advance is False
    assert d.suggest_close_talk == "no"
    assert d.suspect_injection_attempt is False
    assert d.request_human_escalation is None


def test_response_text_min_length() -> None:
    with pytest.raises(ValidationError):
        TurnDecision(
            response_text="",
            collected_fields={},
            reasoning="x",
        )


def test_reasoning_max_length() -> None:
    with pytest.raises(ValidationError):
        TurnDecision(
            response_text="oi",
            collected_fields={},
            reasoning="x" * 500,  # > 400 char max
        )


def test_human_escalation_categories_validated() -> None:
    e = HumanEscalation(
        reason="lead asked complex question I can't answer",
        category="unknown_info",
        urgency="medium",
    )
    assert e.category == "unknown_info"
    with pytest.raises(ValidationError):
        HumanEscalation(
            reason="reason ok",
            category="not_a_category",  # type: ignore[arg-type]
            urgency="medium",
        )


def test_turn_decision_with_escalation() -> None:
    d = TurnDecision(
        response_text="Vou conferir com a equipe e te volto",
        collected_fields={},
        reasoning="lead asked about regulatory edge case beyond scope",
        request_human_escalation=HumanEscalation(
            reason="regulatory question outside training data",
            category="out_of_scope",
            urgency="medium",
            waiting_message="vou conferir e volto",
        ),
    )
    assert d.request_human_escalation is not None
    assert d.request_human_escalation.urgency == "medium"


def test_judge_verdict_round_trip() -> None:
    v = JudgeVerdict(should_exit=True, reasoning="all qualifying fields collected")
    reloaded = JudgeVerdict.model_validate(v.model_dump(mode="json"))
    assert reloaded.should_exit is True


def test_suggest_close_talk_literals() -> None:
    """Closure signal only accepts known closure types."""
    d = TurnDecision(
        response_text="combinado, te aviso!",
        collected_fields={},
        reasoning="lead confirmed demo",
        suggest_close_talk="completed_success",
    )
    assert d.suggest_close_talk == "completed_success"
    with pytest.raises(ValidationError):
        TurnDecision(
            response_text="x",
            collected_fields={},
            reasoning="y",
            suggest_close_talk="random",  # type: ignore[arg-type]
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_flowengine_decision_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_sdr.flowengine.decision'`.

- [ ] **Step 3: Create the module**

Create `src/ai_sdr/flowengine/decision.py`:

```python
"""Pydantic schemas for the main LLM's structured output.

TurnDecision is the single shape the main LLM returns each turn. Every
side-effect the FlowEngine takes after the call is driven from fields
here: what to say (response_text), which fields to record
(collected_fields, extracted_facts), what state changes to enact
(next_node_suggestion, suggest_close_talk, request_human_escalation),
and self-attestations the LLM owes the runtime (treatment_resolved,
suspect_injection_attempt, reasoning).

The schema is bound via `with_structured_output(TurnDecision)` on the
main LLM in FE-01b.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TreatmentStrategy = Literal["inline", "subnode", "tool"]
CloseTalkSignal = Literal[
    "no", "completed_success", "completed_failure", "no_interest"
]
ResponseFormat = Literal["text", "voice", "both"]
EscalationCategory = Literal[
    "unknown_info",
    "out_of_scope",
    "complex_objection",
    "lead_requested",
    "sensitive_topic",
    "ambiguous_intent",
    "system_exhausted",
    "other",
]
Urgency = Literal["low", "medium", "high"]


class HumanEscalation(BaseModel):
    """Set on TurnDecision.request_human_escalation when the LLM asks for help."""

    reason: str = Field(min_length=10, max_length=300)
    category: EscalationCategory
    urgency: Urgency
    suggested_response: str | None = None
    waiting_message: str | None = None


class TurnDecision(BaseModel):
    """The single structured output of the main LLM per turn."""

    # The response to send to the lead
    response_text: str = Field(min_length=1)
    response_format: ResponseFormat | None = None
    voice_emotion: str | None = None

    # Fields extracted from this turn (per current node's `collects` schema)
    collected_fields: dict[str, Any]

    # Optional facts about the lead (short-term memory)
    extracted_facts: dict[str, Any] = Field(default_factory=dict)

    # Objection detection
    detected_objection: str | None = None
    treatment_strategy: TreatmentStrategy | None = None

    # Treatment resolution (when active_treatment was in progress)
    treatment_resolved: bool = False

    # Routing
    next_node_suggestion: str | None = None
    intends_to_advance: bool = False

    # Talk closure signal
    suggest_close_talk: CloseTalkSignal = "no"

    # Human escalation
    request_human_escalation: HumanEscalation | None = None

    # Prompt injection self-flag
    suspect_injection_attempt: bool = False

    # Reasoning (audit + debugging)
    reasoning: str = Field(min_length=1, max_length=400)


class JudgeVerdict(BaseModel):
    """Dedicated exit_condition judge LLM response (see spec §11.2)."""

    should_exit: bool
    reasoning: str = Field(min_length=1, max_length=200)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_flowengine_decision_schema.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/decision.py tests/unit/test_flowengine_decision_schema.py
git commit -m "feat(flowengine): Pydantic TurnDecision + HumanEscalation + JudgeVerdict"
```

---

## Task 20: LeadRepository

**Files:**
- Create: `src/ai_sdr/repositories/__init__.py` (only if missing)
- Create: `src/ai_sdr/repositories/lead_repository.py`
- Create: `tests/integration/test_lead_repository.py`

Thin async repository for Lead with helpers FE-01b will need: lookup by channel identifier (per spec §4 step 3 "Resolve Lead from inbound message").

- [ ] **Step 1: Create the package marker if missing**

Check if `src/ai_sdr/repositories/__init__.py` exists. If not, create it with content:

```python
"""Repositories — thin async DB access wrappers around SQLAlchemy models."""
```

- [ ] **Step 2: Write the failing test**

Create `tests/integration/test_lead_repository.py`:

```python
"""LeadRepository — lookup + identity field updates."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.repositories.lead_repository import LeadRepository


@pytest.mark.asyncio
async def test_find_by_channel_identifier(db_session: AsyncSession) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    lead = Lead(
        tenant_id=tenant.id,
        channel_identifiers={"whatsapp": "+5511999999999"},
    )
    db_session.add(lead)
    await db_session.flush()

    repo = LeadRepository(db_session)
    found = await repo.find_by_channel_identifier(
        tenant.id, "whatsapp", "+5511999999999"
    )
    assert found is not None
    assert found.id == lead.id


@pytest.mark.asyncio
async def test_find_by_channel_identifier_returns_none_when_missing(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    repo = LeadRepository(db_session)
    assert (
        await repo.find_by_channel_identifier(tenant.id, "whatsapp", "+nope")
        is None
    )


@pytest.mark.asyncio
async def test_set_risk_level_updates_audit_columns(
    db_session: AsyncSession,
) -> None:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    await db_session.flush()

    repo = LeadRepository(db_session)
    await repo.set_risk_level(lead, "elevated", reason="spamming")
    await db_session.flush()
    assert lead.risk_level == "elevated"
    assert lead.risk_level_reason == "spamming"
    assert lead.risk_level_since is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_lead_repository.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Create the repository**

Create `src/ai_sdr/repositories/lead_repository.py`:

```python
"""LeadRepository — thin async helpers around Lead.

FE-01a ships the minimum FE-01b needs:
  - find_by_channel_identifier (worker resolves Lead from inbound payload)
  - set_risk_level (Sentinel transitions risk_level + reason atomically)

Heavier queries (lead listing, search) belong to FE-06 API surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead


class LeadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_channel_identifier(
        self,
        tenant_id: uuid.UUID,
        channel: str,
        identifier: str,
    ) -> Lead | None:
        """Look up a Lead by a single channel identifier.

        Operates under the caller's tenant context (RLS); the explicit
        tenant_id filter is belt-and-suspenders.
        """
        stmt = select(Lead).where(
            Lead.tenant_id == tenant_id,
            text("leads.channel_identifiers ->> :ch = :v"),
        ).params(ch=channel, v=identifier)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_risk_level(
        self,
        lead: Lead,
        level: str,
        reason: str | None = None,
    ) -> None:
        """Transition a Lead's risk_level + record reason + timestamp."""
        if level not in ("normal", "elevated", "banned"):
            raise ValueError(f"invalid risk_level: {level!r}")
        lead.risk_level = level
        lead.risk_level_reason = reason
        lead.risk_level_since = datetime.now(timezone.utc)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_lead_repository.py -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/repositories/__init__.py src/ai_sdr/repositories/lead_repository.py tests/integration/test_lead_repository.py
git commit -m "feat(repositories): LeadRepository — channel lookup + risk_level transition"
```

---

## Task 21: TalkRepository

**Files:**
- Create: `src/ai_sdr/repositories/talk_repository.py`
- Create: `tests/integration/test_talk_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_talk_repository.py`:

```python
"""TalkRepository — active Talk lookup + creation helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talk_repository import TalkRepository


async def _seed_tenant_lead_treeflow(
    db_session: AsyncSession,
) -> tuple[Tenant, Lead, TreeflowVersion]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    return tenant, lead, tfv


@pytest.mark.asyncio
async def test_find_active_for_lead_returns_active_talk(
    db_session: AsyncSession,
) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    repo = TalkRepository(db_session)
    assert await repo.find_active_for_lead(tenant.id, lead.id) is None

    talk = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="active", handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()

    found = await repo.find_active_for_lead(tenant.id, lead.id)
    assert found is not None
    assert found.id == talk.id


@pytest.mark.asyncio
async def test_find_active_ignores_closed_talks(db_session: AsyncSession) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    closed = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="closed_completed",
        handling_mode="ai", last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(closed)
    await db_session.flush()

    repo = TalkRepository(db_session)
    assert await repo.find_active_for_lead(tenant.id, lead.id) is None


@pytest.mark.asyncio
async def test_create_talk_initializes_defaults(db_session: AsyncSession) -> None:
    tenant, lead, tfv = await _seed_tenant_lead_treeflow(db_session)
    repo = TalkRepository(db_session)
    talk = await repo.create(
        tenant_id=tenant.id,
        lead_id=lead.id,
        treeflow_id="tf",
        treeflow_version_id=tfv.id,
    )
    await db_session.flush()
    assert talk.status == "active"
    assert talk.handling_mode == "ai"
    assert talk.turn_count == 0
    assert talk.tokens_consumed == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_talk_repository.py -v`
Expected: FAIL.

- [ ] **Step 3: Create the repository**

Create `src/ai_sdr/repositories/talk_repository.py`:

```python
"""TalkRepository — active Talk lookup + creation helpers.

FE-01a ships:
  - find_active_for_lead (worker preprocessing per spec §4 step 3)
  - create (new inbound from Lead with no active Talk)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.talk import Talk

ACTIVE_STATUSES = {"active", "paused", "requires_review"}


class TalkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_active_for_lead(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> Talk | None:
        """Return the open Talk for this Lead, if any.

        V1 invariant: at most one active Talk per (tenant, lead).
        """
        stmt = (
            select(Talk)
            .where(
                Talk.tenant_id == tenant_id,
                Talk.lead_id == lead_id,
                Talk.status.in_(tuple(ACTIVE_STATUSES)),
            )
            .order_by(Talk.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        treeflow_id: str,
        treeflow_version_id: uuid.UUID,
    ) -> Talk:
        """Create a new active Talk bound to a TreeFlow version snapshot."""
        now = datetime.now(timezone.utc)
        talk = Talk(
            tenant_id=tenant_id,
            lead_id=lead_id,
            treeflow_id=treeflow_id,
            treeflow_version_id=treeflow_version_id,
            status="active",
            handling_mode="ai",
            last_message_at=now,
        )
        self._session.add(talk)
        return talk
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_talk_repository.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/talk_repository.py tests/integration/test_talk_repository.py
git commit -m "feat(repositories): TalkRepository — active lookup + create"
```

---

## Task 22: TalkFlowStateRepository

**Files:**
- Create: `src/ai_sdr/repositories/talkflow_state_repository.py`
- Create: `tests/integration/test_talkflow_state_repository.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_talkflow_state_repository.py`:

```python
"""TalkFlowStateRepository — load + initialize + append_message."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.flowengine.state import Message
from ai_sdr.models.lead import Lead
from ai_sdr.models.talk import Talk
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.repositories.talkflow_state_repository import TalkFlowStateRepository


async def _seed_talk(db_session: AsyncSession) -> Talk:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="t")
    db_session.add(tenant)
    await db_session.flush()
    lead = Lead(tenant_id=tenant.id)
    db_session.add(lead)
    tfv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="tf", version="1",
        content_hash="x", content_yaml="y",
    )
    db_session.add(tfv)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant.id)},
    )
    talk = Talk(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_id="tf",
        treeflow_version_id=tfv.id, status="active", handling_mode="ai",
        last_message_at=datetime.now(timezone.utc),
    )
    db_session.add(talk)
    await db_session.flush()
    return talk


@pytest.mark.asyncio
async def test_load_returns_none_before_init(db_session: AsyncSession) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    assert await repo.load(talk.id) is None


@pytest.mark.asyncio
async def test_initialize_creates_default_state(db_session: AsyncSession) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(
        talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao"
    )
    await db_session.flush()
    assert state.current_node == "saudacao"
    assert state.collected == {}
    assert state.messages == []
    assert state.objections_handled == []
    assert state.active_treatment is None


@pytest.mark.asyncio
async def test_append_message_grows_rolling_window(
    db_session: AsyncSession,
) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(
        talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao"
    )
    await db_session.flush()

    m = Message(
        role="user", content="oi", source="lead",
        turn_index=1, timestamp=datetime.now(timezone.utc),
    )
    await repo.append_message(state, m, max_window=15)
    await db_session.flush()
    assert len(state.messages) == 1
    assert state.messages[0]["content"] == "oi"


@pytest.mark.asyncio
async def test_append_message_evicts_when_window_exceeded(
    db_session: AsyncSession,
) -> None:
    talk = await _seed_talk(db_session)
    repo = TalkFlowStateRepository(db_session)
    state = await repo.initialize(
        talk_id=talk.id, tenant_id=talk.tenant_id, entry_node="saudacao"
    )
    await db_session.flush()

    for i in range(1, 18):
        m = Message(
            role="user", content=f"msg-{i}", source="lead",
            turn_index=i, timestamp=datetime.now(timezone.utc),
        )
        await repo.append_message(state, m, max_window=15)
    await db_session.flush()
    assert len(state.messages) == 15
    assert state.messages[0]["content"] == "msg-3"  # oldest two evicted
    assert state.messages[-1]["content"] == "msg-17"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_talkflow_state_repository.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the repository**

Create `src/ai_sdr/repositories/talkflow_state_repository.py`:

```python
"""TalkFlowStateRepository — load + initialize + message append.

FE-01a ships the minimum FE-01b needs to read state, seed it on new Talk,
and grow the rolling message window. Heavier mutations (treatment
lifecycle, objection history append) live in feature-specific modules.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ai_sdr.flowengine.state import Message
from ai_sdr.models.talkflow_state import TalkFlowState


class TalkFlowStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, talk_id: uuid.UUID) -> TalkFlowState | None:
        stmt = select(TalkFlowState).where(TalkFlowState.talk_id == talk_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def initialize(
        self,
        *,
        talk_id: uuid.UUID,
        tenant_id: uuid.UUID,
        entry_node: str,
    ) -> TalkFlowState:
        """Create the runtime state row for a freshly opened Talk."""
        state = TalkFlowState(
            talk_id=talk_id,
            tenant_id=tenant_id,
            current_node=entry_node,
            collected={},
            extracted_facts={},
            messages=[],
            objections_handled=[],
            talkflow_stack=[],
        )
        self._session.add(state)
        return state

    async def append_message(
        self,
        state: TalkFlowState,
        message: Message,
        *,
        max_window: int,
    ) -> None:
        """Append a Message to the rolling window, evicting oldest as needed.

        The TalkFlowState.messages JSONB list is mutated in-place; we mark
        it modified so SQLAlchemy flushes the change.
        """
        if max_window < 1:
            raise ValueError("max_window must be >= 1")
        payload = message.model_dump(mode="json")
        # `state.messages` is the live JSONB list; treat as mutable.
        current = list(state.messages)
        current.append(payload)
        if len(current) > max_window:
            current = current[-max_window:]
        state.messages = current
        flag_modified(state, "messages")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_talkflow_state_repository.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/talkflow_state_repository.py tests/integration/test_talkflow_state_repository.py
git commit -m "feat(repositories): TalkFlowStateRepository — load/init/append_message"
```

---

## Task 23: Verify all migrations apply cleanly on a fresh DB

**Files:**
- Create: `tests/integration/test_all_migrations_apply_clean.py`

End-of-plan acceptance: with an empty database, `alembic upgrade head` must reach 0023, leaving the schema usable. Downgrade from head must also succeed back to 0011 (FE-01a baseline = pre-FE-01a state).

- [ ] **Step 1: Write the test**

Create `tests/integration/test_all_migrations_apply_clean.py`:

```python
"""End-of-plan: all FE-01a migrations apply + roll back cleanly.

Run manually with a *fresh* database. This test is marked so CI can opt
in selectively. The body uses alembic's Python API to run a full
upgrade + downgrade roundtrip on a transient database URL provided via
the env var TEST_FRESH_DB_URL.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.fresh_db
def test_upgrade_head_then_downgrade_to_0011() -> None:
    test_url = os.environ.get("TEST_FRESH_DB_URL")
    if not test_url:
        pytest.skip("TEST_FRESH_DB_URL not set — skipping fresh-DB acceptance")

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_url)

    command.upgrade(cfg, "head")
    # Sanity: head is 0023
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    assert head == "0023_add_tenant_architecture_version"

    command.downgrade(cfg, "0011_outbound_messages")
```

- [ ] **Step 2: Register the marker**

Edit `pyproject.toml` and add `fresh_db` to the pytest markers list. Find the existing `[tool.pytest.ini_options]` section's `markers` array and add `"fresh_db: requires a transient fresh database (set TEST_FRESH_DB_URL)"` to it.

If `pyproject.toml` does not have a `markers` array, add one:

```toml
[tool.pytest.ini_options]
markers = [
    "fresh_db: requires a transient fresh database (set TEST_FRESH_DB_URL)",
]
```

- [ ] **Step 3: Smoke run the test (skipped without env)**

Run: `python -m pytest tests/integration/test_all_migrations_apply_clean.py -v`
Expected: SKIPPED (no `TEST_FRESH_DB_URL`).

- [ ] **Step 4: Manually verify on a fresh DB**

Spin up a transient Postgres (Docker, or whatever the developer's environment provides):

```bash
docker run -d --rm --name fe01a-test-pg \
  -e POSTGRES_PASSWORD=test -e POSTGRES_USER=test -e POSTGRES_DB=test \
  -p 15433:5432 postgres:16-alpine
sleep 5
psql postgresql://test:test@localhost:15433/test -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""
TEST_FRESH_DB_URL=postgresql+psycopg2://test:test@localhost:15433/test \
  python -m pytest tests/integration/test_all_migrations_apply_clean.py -v
docker stop fe01a-test-pg
```

Expected: PASS — upgrade reaches 0023 and downgrade back to 0011 succeeds.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_all_migrations_apply_clean.py pyproject.toml
git commit -m "test(migration): fresh-DB acceptance for FE-01a migrations 0012..0023"
```

- [ ] **Step 6: Verify entire test suite**

Run the full project test suite to confirm no regressions:

```bash
python -m pytest tests/ -v --tb=short
```

Expected: every previously passing test still passes. Newly added tests (this plan) PASS. Tests marked `fresh_db` SKIP (no env var). Tests marked `live_llm` SKIP. No failures.

If any pre-existing test fails, treat it as a regression: investigate root cause before declaring the plan complete.

- [ ] **Step 7: Final commit (if any docs/notes touched during regression sweep)**

Only if the regression sweep surfaces a small note worth saving (e.g., updating a docstring that no longer matches a field), commit it:

```bash
git add <files>
git commit -m "chore: doc/comment fixes surfaced by FE-01a regression sweep"
```

Otherwise skip this step.

---

## Acceptance criteria

This plan is complete when ALL of the following hold:

1. `alembic current` shows head = `0023_add_tenant_architecture_version`.
2. All 11 new migrations (`0012`..`0023`) have corresponding integration tests that PASS.
3. `python -m pytest tests/unit/test_flowengine_decision_schema.py tests/unit/test_flowengine_state_schema.py -v` shows ALL Pydantic schemas PASS validation tests.
4. `python -m pytest tests/integration/test_lead_repository.py tests/integration/test_talk_repository.py tests/integration/test_talkflow_state_repository.py -v` shows ALL repositories PASS.
5. `python -m pytest tests/ -v` shows no regressions in pre-existing tests.
6. Fresh-DB acceptance test (Task 23 Step 4) passes locally.
7. `git log --oneline 0023..HEAD` (or however you tag the FE-01a baseline) shows commits structured one-per-task.

## Files NOT modified (sanity check)

If any of these files have unexpected diffs at the end of the plan, something went wrong:

- `src/ai_sdr/treeflow/` — entire directory is LangGraph code, FE-02 deletes it
- `src/ai_sdr/guardrails/critic.py` — LLM critic, FE-02 deletes alongside LangGraph
- `src/ai_sdr/workers/jobs/inbound.py` — pipeline v1 entrypoint, FE-01b adds the v2 branch
- `src/ai_sdr/models/user.py` — P11 operator model, do NOT modify
- `src/ai_sdr/models/user_tenant_access.py` — P11, do NOT modify
- `src/ai_sdr/messaging/` — preserved (FE-05 generalizes via adapter framework)

---

## Next plan

FE-01b — Core Pipeline. Builds the orchestrator function that uses every schema this plan laid down: preprocessing (Lead/Talk resolution), layered system prompt builder, main LLM call via `with_structured_output(TurnDecision)`, transition routing with corrective retry, Python critic validator (replaces critic LLM in v2 path only), and the architecture_version feature flag in `process_lead_inbox` that routes to v1 (LangGraph, default) or v2 (FlowEngine). LangGraph stays alive until FE-02.
