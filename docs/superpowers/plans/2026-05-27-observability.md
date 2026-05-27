# Observability Implementation Plan (Plano 10)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LangSmith tracing on all 4 LLM call sites + persist every adapter send (text and template, success and failure) into a new `outbound_messages` table. After this plan, every LLM call is filterable in the LangSmith dashboard by tenant/lead/talkflow/origin, and every message PeSDR sends to a lead is queryable via `ai-sdr outbound list` for audit, debug, and the future conversation viewer (P11b).

**Architecture:** LangSmith tracing is opt-in via 3 env vars (`LANGCHAIN_TRACING_V2`, `LANGSMITH_API_KEY`, `LANGCHAIN_PROJECT`) — langchain-core auto-captures all chain runs when enabled. We thread a `build_trace_metadata()` helper into the 4 existing `ainvoke` calls so traces carry `{tenant_id, tenant_slug, talkflow_id, lead_id, node, turn_index, trace_origin}` for filtering. Outbound audit lives in a new `outbound_messages` table (RLS, partial indexes on `(lead_id, sent_at DESC)` and `(tenant_id, sent_at DESC)`) with a XOR check constraint that text rows carry `body_text` and template rows carry `template_ref`. Two helper functions (`record_outbound_sent`, `record_outbound_failed`) live in `src/ai_sdr/observability/outbound_audit.py` and are called by the worker (Plan 5 `_process_one` paths + Plan 9 WindowExpired recovery) and the Plan 9 scanner. Helpers flush only — the caller commits as part of its own transaction.

**Tech Stack additions:** `langsmith` (already transitive via `langchain==1.3.0` — version 0.8.5 in uv.lock; no `pyproject.toml` change). No other new deps.

**Spec:** [`docs/superpowers/specs/2026-05-27-observability-design.md`](../specs/2026-05-27-observability-design.md). Read §3 (não-objetivos), §5 (modelo de dados), §6 (LangSmith setup), §7 (audit helpers + write sites), §10 (testing) before starting.

---

## File Structure

```
src/ai_sdr/
├── observability/                          # NEW package
│   ├── __init__.py                         # NEW (empty)
│   ├── tracing.py                          # NEW: build_trace_metadata
│   └── outbound_audit.py                   # NEW: record_outbound_sent + record_outbound_failed
│
├── models/
│   ├── outbound_message.py                 # NEW: OutboundMessage ORM
│   └── __init__.py                         # MODIFIED: re-export OutboundMessage
│
├── treeflow/
│   ├── runtime.py                          # MODIFIED: graph.ainvoke at line ~234 gets config metadata
│   └── classifier.py                       # MODIFIED: structured.ainvoke at line ~84 gets config metadata
│
├── llm/
│   └── extractor.py                        # MODIFIED: runnable.ainvoke at line ~82 gets config metadata
│
├── guardrails/
│   └── critic.py                           # MODIFIED: runnable.ainvoke at line ~96 gets config metadata
│
├── worker/
│   └── jobs/
│       ├── inbound.py                      # MODIFIED: 6 audit write sites (text send_text + WindowExpired recovery)
│       └── follow_up_scanner.py            # MODIFIED: 4 audit write sites (1 success + 3 failure)
│
├── cli/
│   ├── outbound.py                         # NEW: ai-sdr outbound list
│   └── app.py                              # MODIFIED: register outbound_app
│
├── settings.py                             # MODIFIED: 3 new fields (langchain_tracing_v2, langsmith_api_key, langchain_project)
└── main.py                                 # MODIFIED: startup validator for LangSmith config

migrations/versions/
└── 0011_outbound_messages.py               # NEW

docker-compose.yml                          # MODIFIED: API + worker services get 3 langchain env vars
.env.example                                # MODIFIED (or created): 3 LangSmith vars commented
pyproject.toml                              # UNCHANGED — langsmith already transitive (uv.lock has it)
CLAUDE.md                                   # MODIFIED: new "Observability (Plano 10)" section

tests/
├── unit/
│   ├── test_observability_tracing_metadata.py  # NEW
│   ├── test_outbound_audit_helpers.py          # NEW
│   └── test_outbound_cli.py                    # NEW
└── integration/
    ├── test_outbound_messages_model.py                          # NEW
    ├── test_outbound_audit_writes_from_inbound.py               # NEW
    ├── test_outbound_audit_writes_from_send_failure.py          # NEW
    ├── test_outbound_audit_writes_from_window_expired_recovery.py # NEW
    ├── test_outbound_audit_writes_from_follow_up_scanner.py     # NEW
    ├── test_outbound_cli_integration.py                         # NEW
    └── test_langsmith_live.py                                   # NEW (gated by live_llm marker)
```

**Layout notes:**
- `observability/` is a brand-new sibling package to `messaging/`, `follow_up/`, `worker/`. Two small modules: `tracing.py` (metadata helper) and `outbound_audit.py` (DB write helpers). Both pure — no I/O state, no global config.
- The 4 LLM call site edits (runtime, classifier, extractor, critic) are all the same shape: existing `ainvoke(messages)` → `ainvoke(messages, config={"metadata": build_trace_metadata(...)})`. Five lines per edit. **Bundled into a single task** (Task 6) because they're cohesive and trivially independent.
- The 10 audit write sites are spread across two files (`worker/jobs/inbound.py` for the P5 + WindowExpired recovery paths, `worker/jobs/follow_up_scanner.py` for the P9 path) — split into 3 tasks (T7, T8, T9) by **causation**, not by file, so each task lands a coherent feature.

---

## Prerequisites (delta from Plan 9)

Plan 5 + 9 prereqs (Docker, uv, Postgres on VPS port 15432, Redis on 16379) still apply. **No new ENV vars are required to run P10** — LangSmith is opt-in. For local dev with LangSmith enabled:

```bash
# Optional — only when you want LangSmith tracing locally:
LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=ls__<your-key>           # https://smith.langchain.com → API Keys
LANGCHAIN_PROJECT=pesdr-dev                # or pesdr-prod on VPS
```

### Migration dependency

Migration 0011 depends on **0010** from Plan 9 being applied (the `follow_up_jobs` table exists — `outbound_messages.follow_up_job_id` has a FK to it). If Plan 9 hasn't merged to `dev/nicolas` yet when this plan executes, the migration coordinator must order the trunk integration: P9 first, then P10.

### Shared test fixtures

`tests/conftest.py` from Plan 5 (with `db_session` + `app` fixtures, NullPool, session-scoped event loop) is reused as-is.

### VPS notes

After deploying:
1. Set the 3 LangSmith env vars in the VPS `.env` (or leave unset — tracing stays off).
2. Apply migration: `uv run alembic upgrade head`.
3. Restart API + worker: `docker compose up -d --build api worker`.

If LangSmith is enabled, traces start flowing within seconds of the next LLM call. No code-side cron or warmup.

---

## Task 1: Migration 0011 — `outbound_messages` table

**Files:**
- Create: `migrations/versions/0011_outbound_messages.py`

**Design:** Per spec §5: tenant-scoped RLS table that mirrors P5's `inbound_messages` shape but for the outgoing direction. Two partial indexes (lead-scoped and tenant-scoped, both ordered by `sent_at DESC`) cover the common query patterns from the CLI + future conversation viewer (P11b). XOR check constraint enforces `message_type` consistency at DB level — no Python validator needed.

- [ ] **Step 1: Create the migration file**

Create `migrations/versions/0011_outbound_messages.py`:

```python
"""outbound_messages table (with RLS + XOR constraint + partial indexes)

Revision ID: 0011_outbound_messages
Revises: 0010_follow_up_and_talkflow_columns
Create Date: 2026-05-27 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0011_outbound_messages"
down_revision = "0010_follow_up_and_talkflow_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("talkflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=False),

        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),

        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("template_ref", sa.Text(), nullable=True),
        sa.Column("template_language", sa.Text(), nullable=True),
        sa.Column("template_params", JSONB(), nullable=True),

        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),

        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("follow_up_job_id", UUID(as_uuid=True), nullable=True),

        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["inbound_message_id"], ["inbound_messages.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["follow_up_job_id"], ["follow_up_jobs.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "message_type IN ('text', 'template')",
            name="ck_outbound_message_type",
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed')",
            name="ck_outbound_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery')",
            name="ck_outbound_triggered_by",
        ),
        sa.CheckConstraint(
            "(message_type = 'text' AND body_text IS NOT NULL AND template_ref IS NULL) "
            "OR (message_type = 'template' AND template_ref IS NOT NULL AND body_text IS NULL)",
            name="ck_outbound_body_consistency",
        ),
    )

    op.create_index(
        "ix_outbound_messages_lead_sent",
        "outbound_messages",
        ["lead_id", sa.text("sent_at DESC")],
    )
    op.create_index(
        "ix_outbound_messages_tenant_sent",
        "outbound_messages",
        ["tenant_id", sa.text("sent_at DESC")],
    )

    op.execute("ALTER TABLE outbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY outbound_messages_tenant_isolation ON outbound_messages
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS outbound_messages_tenant_isolation ON outbound_messages")
    op.drop_index("ix_outbound_messages_tenant_sent", table_name="outbound_messages")
    op.drop_index("ix_outbound_messages_lead_sent", table_name="outbound_messages")
    op.drop_table("outbound_messages")
```

- [ ] **Step 2: Verify revision chain**

Run: `uv run python -c "from alembic.script import ScriptDirectory; from alembic.config import Config; sd = ScriptDirectory.from_config(Config('alembic.ini')); print([s.revision for s in sd.walk_revisions()])"`

Expected: list includes `'0011_outbound_messages'` as tip after `'0010_follow_up_and_talkflow_columns'`. No import errors loading the migration module.

- [ ] **Step 3: Skip local apply (Docker on VPS)**

Controller pushes the branch and runs `alembic upgrade head` on the VPS as part of validation.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0011_outbound_messages.py
git commit -m "$(cat <<'EOF'
feat(plan10 t1): migration 0011 — outbound_messages table

Tenant-scoped RLS table mirroring P5 inbound_messages for the outgoing
direction. XOR check constraint enforces text vs template consistency
at DB level (no Python validator). Two partial indexes ordered by
sent_at DESC cover lead-scoped (conversation history) and tenant-scoped
(CLI list) query patterns. FKs to inbound_messages and follow_up_jobs
are ON DELETE SET NULL so cleanup of source rows doesn't break audit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `OutboundMessage` ORM + integration tests

**Files:**
- Create: `src/ai_sdr/models/outbound_message.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `tests/integration/test_outbound_messages_model.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_outbound_messages_model.py`:

```python
"""OutboundMessage ORM — RLS, FK cascades, XOR check, triggered_by enum."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead]:
    tenant = Tenant(slug=f"o_{uuid.uuid4().hex[:6]}", display_name="O")
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

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    return tenant, tf, lead


async def test_create_text_message(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="Olá",
        status="sent",
        external_id="wamid.X",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    assert row.id is not None
    assert row.created_at is not None


async def test_create_template_message(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="template",
        template_ref="followup_24h_v1",
        template_language="pt_BR",
        template_params=["amigo"],
        status="sent",
        external_id="wamid.Y",
        triggered_by="follow_up_scanner",
        sent_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    assert row.template_params == ["amigo"]


async def test_xor_check_text_with_template_ref_fails(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="Olá",
        template_ref="should_not_be_here",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(Exception):  # ck_outbound_body_consistency
        await db_session.commit()
    await db_session.rollback()


async def test_xor_check_template_missing_ref_fails(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="template",
        # no template_ref — must fail
        status="sent",
        triggered_by="follow_up_scanner",
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_triggered_by_enum_rejects_invalid(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="x",
        status="sent",
        triggered_by="manual_takeover",  # not yet a valid enum value
        sent_at=datetime.now(UTC),
    ))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()


async def test_rls_blocks_cross_tenant_read(db_session) -> None:
    tenant_a, tf_a, lead_a = await _seed(db_session)
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant_a.id, talkflow_id=tf_a.id, lead_id=lead_a.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="visible only to tenant A",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    tenant_b = Tenant(slug=f"b_{uuid.uuid4().hex[:6]}", display_name="B")
    db_session.add(tenant_b)
    await db_session.commit()
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(OutboundMessage))).scalars().all()
    assert rows == []


async def test_lead_cascade_delete_removes_outbound(db_session) -> None:
    tenant, tf, lead = await _seed(db_session)
    await set_tenant_context(db_session, tenant.id)
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud",
        message_type="text",
        body_text="bye",
        status="sent",
        triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    await db_session.delete(lead)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)
    rows = (await db_session.execute(select(OutboundMessage))).scalars().all()
    assert rows == []
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_outbound_messages_model.py -v`

Expected: FAIL — `ImportError: cannot import name 'OutboundMessage'`.

- [ ] **Step 3: Create the ORM model**

Create `src/ai_sdr/models/outbound_message.py`:

```python
"""OutboundMessage — audit row for each adapter send (success or failure).

Tenant-scoped (RLS), FK to talkflow + lead, optional FKs to the
inbound_message or follow_up_job that triggered the send. XOR check
constraint at the DB level ensures text rows carry body_text and
template rows carry template_ref.

Worker (Plan 5 + 9 paths) and scanner (Plan 9) insert via the helpers
in ai_sdr.observability.outbound_audit. CLI + future conversation
viewer (P11b) read via standard SELECT under tenant context.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"

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

    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    message_type: Mapped[str] = mapped_column(Text(), nullable=False)

    body_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_ref: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_language: Mapped[str | None] = mapped_column(Text(), nullable=True)
    template_params: Mapped[list[str] | None] = mapped_column(JSONB(), nullable=True)

    status: Mapped[str] = mapped_column(Text(), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)

    triggered_by: Mapped[str] = mapped_column(Text(), nullable=False)
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbound_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    follow_up_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("follow_up_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 4: Re-export from `models/__init__.py`**

Open `src/ai_sdr/models/__init__.py`. Add the import + entry in `__all__` (alphabetical):

```python
from ai_sdr.models.outbound_message import OutboundMessage
```

…and add `"OutboundMessage"` to `__all__`.

- [ ] **Step 5: Verify locally**

Run: `uv run python -c "from ai_sdr.models import OutboundMessage; print(OutboundMessage.__tablename__)"`

Expected: prints `outbound_messages`.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/outbound_message.py src/ai_sdr/models/__init__.py tests/integration/test_outbound_messages_model.py
git commit -m "$(cat <<'EOF'
feat(plan10 t2): OutboundMessage ORM model + integration tests

Mirrors the migration 0011 shape. Audit row inserted by the helpers
in ai_sdr.observability.outbound_audit (Task 4); read by the CLI
(Task 10) and future conversation viewer (P11b). FKs ON DELETE
CASCADE for tenant/talkflow/lead and SET NULL for source rows
(inbound_message_id, follow_up_job_id) so audit survives source
cleanup.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `observability/tracing.py` — `build_trace_metadata` helper

**Files:**
- Create: `src/ai_sdr/observability/__init__.py` (empty)
- Create: `src/ai_sdr/observability/tracing.py`
- Create: `tests/unit/test_observability_tracing_metadata.py`

**Design:** Per spec §6: `build_trace_metadata(*, tenant=None, talkflow=None, lead=None, node=None, turn_index=None, trace_origin) -> dict`. Required: `trace_origin` (Literal). Optional: every other field. The function only includes fields that were passed (no `null` clutter in the LangSmith dashboard).

- [ ] **Step 1: Create the package skeleton**

Run: `mkdir -p src/ai_sdr/observability && touch src/ai_sdr/observability/__init__.py`

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_observability_tracing_metadata.py`:

```python
"""build_trace_metadata — produces dict for langchain RunnableConfig.metadata."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from ai_sdr.observability.tracing import build_trace_metadata


def _tenant(slug="joana"):
    t = SimpleNamespace()
    t.id = uuid.uuid4()
    t.slug = slug
    return t


def _talkflow():
    tf = SimpleNamespace()
    tf.id = uuid.uuid4()
    return tf


def _lead():
    l = SimpleNamespace()
    l.id = uuid.uuid4()
    return l


def test_minimal_only_trace_origin() -> None:
    m = build_trace_metadata(trace_origin="process_lead_inbox")
    assert m == {"trace_origin": "process_lead_inbox"}


def test_full_metadata() -> None:
    t = _tenant("joana")
    tf = _talkflow()
    l = _lead()
    m = build_trace_metadata(
        tenant=t, talkflow=tf, lead=l,
        node="qualificacao", turn_index=3,
        trace_origin="guardrails_critic",
    )
    assert m["trace_origin"] == "guardrails_critic"
    assert m["tenant_id"] == str(t.id)
    assert m["tenant_slug"] == "joana"
    assert m["talkflow_id"] == str(tf.id)
    assert m["lead_id"] == str(l.id)
    assert m["node"] == "qualificacao"
    assert m["turn_index"] == 3


def test_omits_missing_fields() -> None:
    m = build_trace_metadata(
        tenant=_tenant(), trace_origin="objection_classifier",
    )
    # only tenant + trace_origin keys; no talkflow_id / lead_id / node / turn_index
    assert set(m.keys()) == {"trace_origin", "tenant_id", "tenant_slug"}


def test_turn_index_zero_is_included() -> None:
    """turn_index=0 (legitimate first turn) must NOT be treated as falsy."""
    m = build_trace_metadata(trace_origin="process_lead_inbox", turn_index=0)
    assert "turn_index" in m
    assert m["turn_index"] == 0


@pytest.mark.parametrize("origin", [
    "process_lead_inbox",
    "follow_up_scanner",
    "window_expired_recovery",
    "simulate",
    "objection_classifier",
    "guardrails_critic",
    "field_extractor",
])
def test_accepts_all_documented_origins(origin) -> None:
    m = build_trace_metadata(trace_origin=origin)
    assert m["trace_origin"] == origin
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_observability_tracing_metadata.py -v`

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

Create `src/ai_sdr/observability/tracing.py`:

```python
"""LangSmith tracing metadata helper.

build_trace_metadata produces the dict that callers attach to a
langchain ainvoke via `config={"metadata": ...}`. The dict only
includes keys that were passed — empty fields don't appear, so the
LangSmith dashboard isn't cluttered with null values.

trace_origin is REQUIRED (typed Literal). Every other field is
optional. Sub-traces (e.g., classifier inside graph.ainvoke) inherit
metadata from parent — but each site still passes its own
trace_origin so direct filtering (`metadata.trace_origin = "X"`) in
the dashboard works without depending on the parent context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant


TraceOrigin = Literal[
    "process_lead_inbox",
    "follow_up_scanner",
    "window_expired_recovery",
    "simulate",
    "objection_classifier",
    "guardrails_critic",
    "field_extractor",
]


def build_trace_metadata(
    *,
    tenant: "Tenant | None" = None,
    talkflow: "TalkFlow | None" = None,
    lead: "Lead | None" = None,
    node: str | None = None,
    turn_index: int | None = None,
    trace_origin: TraceOrigin,
) -> dict[str, Any]:
    """Build the langchain RunnableConfig.metadata dict.

    Returns a flat dict with only the populated keys. trace_origin is
    always present. Other fields appear only when their corresponding
    argument is not None.
    """
    metadata: dict[str, Any] = {"trace_origin": trace_origin}
    if tenant is not None:
        metadata["tenant_id"] = str(tenant.id)
        metadata["tenant_slug"] = tenant.slug
    if talkflow is not None:
        metadata["talkflow_id"] = str(talkflow.id)
    if lead is not None:
        metadata["lead_id"] = str(lead.id)
    if node is not None:
        metadata["node"] = node
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    return metadata
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_observability_tracing_metadata.py -v`

Expected: all 11 tests PASS (4 + 7 parametrized).

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/observability/__init__.py src/ai_sdr/observability/tracing.py tests/unit/test_observability_tracing_metadata.py
git commit -m "$(cat <<'EOF'
feat(plan10 t3): build_trace_metadata helper for LangSmith tracing

Returns a flat dict {trace_origin, tenant_id?, tenant_slug?,
talkflow_id?, lead_id?, node?, turn_index?} that callers attach to
ainvoke via config.metadata. trace_origin is required Literal — typed
so misspellings are caught at call sites. turn_index=0 is included
(not treated as falsy).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `observability/outbound_audit.py` — record helpers

**Files:**
- Create: `src/ai_sdr/observability/outbound_audit.py`
- Create: `tests/unit/test_outbound_audit_helpers.py`

**Design:** Per spec §7: two helpers, `record_outbound_sent` and `record_outbound_failed`. Each builds an `OutboundMessage` row, adds to session, flushes (to populate `id`), and returns the row. Caller commits as part of its own transaction.

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_outbound_audit_helpers.py`:

```python
"""record_outbound_sent + record_outbound_failed — shape correctness."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)


def _stub_session() -> MagicMock:
    s = MagicMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    return s


def _tenant():
    return SimpleNamespace(id=uuid.uuid4(), slug="t")


def _talkflow():
    return SimpleNamespace(id=uuid.uuid4())


def _lead():
    return SimpleNamespace(id=uuid.uuid4())


async def test_record_sent_text_fills_body_only() -> None:
    db = _stub_session()
    row = await record_outbound_sent(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="text",
        triggered_by="inbound",
        body_text="Olá",
        external_id="wamid.X",
        sent_at=datetime.now(UTC),
        inbound_message_id=uuid.uuid4(),
    )
    assert row.status == "sent"
    assert row.message_type == "text"
    assert row.body_text == "Olá"
    assert row.template_ref is None
    assert row.template_params is None
    assert row.external_id == "wamid.X"
    assert row.error_detail is None
    assert row.triggered_by == "inbound"
    db.add.assert_called_once_with(row)
    db.flush.assert_awaited_once()


async def test_record_sent_template_fills_template_only() -> None:
    db = _stub_session()
    row = await record_outbound_sent(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="template",
        triggered_by="follow_up_scanner",
        template_ref="followup_24h_v1",
        template_language="pt_BR",
        template_params=["amigo"],
        external_id="wamid.Y",
        sent_at=datetime.now(UTC),
        follow_up_job_id=uuid.uuid4(),
    )
    assert row.status == "sent"
    assert row.message_type == "template"
    assert row.template_ref == "followup_24h_v1"
    assert row.template_language == "pt_BR"
    assert row.template_params == ["amigo"]
    assert row.body_text is None


async def test_record_failed_carries_error_detail() -> None:
    db = _stub_session()
    row = await record_outbound_failed(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="text",
        triggered_by="inbound",
        body_text="Olá",
        error_detail="RecipientUnreachable: number not on WA",
        sent_at=datetime.now(UTC),
        inbound_message_id=uuid.uuid4(),
    )
    assert row.status == "failed"
    assert row.error_detail == "RecipientUnreachable: number not on WA"
    assert row.external_id is None


async def test_record_failed_template_carries_template_fields() -> None:
    db = _stub_session()
    row = await record_outbound_failed(
        db,
        tenant=_tenant(), talkflow=_talkflow(), lead=_lead(),
        provider="whatsapp_cloud",
        message_type="template",
        triggered_by="follow_up_scanner",
        template_ref="x_v1",
        template_language="pt_BR",
        template_params=["v"],
        error_detail="PolicyError: ...",
        sent_at=datetime.now(UTC),
        follow_up_job_id=uuid.uuid4(),
    )
    assert row.message_type == "template"
    assert row.template_ref == "x_v1"
    assert row.template_params == ["v"]
    assert row.body_text is None
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_outbound_audit_helpers.py -v`

Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Create `src/ai_sdr/observability/outbound_audit.py`:

```python
"""Audit helpers — insert one OutboundMessage row per adapter send.

Worker (Plan 5 + 9 paths) and scanner (Plan 9) call these immediately
after the adapter call returns (success or raise → except). Helper
only flushes; the caller commits as part of its own transaction so
the audit row goes with the rest of the state updates (msg.status,
talkflow timestamps, follow_up_job mutations).

Known race (spec §7): if the caller's commit fails AFTER the adapter
already sent the message, the audit row is lost. The caller is
expected to emit a warning log identifying the external_id and the
unrecorded send. The Meta message is not retried (would double-send).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.models.outbound_message import OutboundMessage

if TYPE_CHECKING:
    from ai_sdr.models.lead import Lead
    from ai_sdr.models.talkflow import TalkFlow
    from ai_sdr.models.tenant import Tenant


MessageType = Literal["text", "template"]
TriggeredBy = Literal["inbound", "follow_up_scanner", "window_expired_recovery"]


async def record_outbound_sent(
    session: AsyncSession,
    *,
    tenant: "Tenant",
    talkflow: "TalkFlow",
    lead: "Lead",
    provider: str,
    message_type: MessageType,
    triggered_by: TriggeredBy,
    sent_at: datetime,
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    external_id: str | None = None,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a successful send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        provider=provider,
        message_type=message_type,
        body_text=body_text,
        template_ref=template_ref,
        template_language=template_language,
        template_params=template_params,
        status="sent",
        external_id=external_id,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row


async def record_outbound_failed(
    session: AsyncSession,
    *,
    tenant: "Tenant",
    talkflow: "TalkFlow",
    lead: "Lead",
    provider: str,
    message_type: MessageType,
    triggered_by: TriggeredBy,
    error_detail: str,
    sent_at: datetime,
    body_text: str | None = None,
    template_ref: str | None = None,
    template_language: str | None = None,
    template_params: list[str] | None = None,
    inbound_message_id: uuid.UUID | None = None,
    follow_up_job_id: uuid.UUID | None = None,
) -> OutboundMessage:
    """Insert a failed send audit row. Caller commits."""
    row = OutboundMessage(
        tenant_id=tenant.id,
        talkflow_id=talkflow.id,
        lead_id=lead.id,
        provider=provider,
        message_type=message_type,
        body_text=body_text,
        template_ref=template_ref,
        template_language=template_language,
        template_params=template_params,
        status="failed",
        error_detail=error_detail,
        triggered_by=triggered_by,
        inbound_message_id=inbound_message_id,
        follow_up_job_id=follow_up_job_id,
        sent_at=sent_at,
    )
    session.add(row)
    await session.flush()
    return row
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_outbound_audit_helpers.py -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/observability/outbound_audit.py tests/unit/test_outbound_audit_helpers.py
git commit -m "$(cat <<'EOF'
feat(plan10 t4): outbound audit helpers (record_outbound_sent / failed)

Two async helpers shared by worker and scanner. Each builds an
OutboundMessage, adds to session, flushes to populate id, returns
the row. Caller commits as part of its own transaction so the audit
row goes with the rest of the state updates atomically (when commit
succeeds).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Settings + startup validator for LangSmith

**Files:**
- Modify: `src/ai_sdr/settings.py`
- Modify: `src/ai_sdr/main.py`
- Create: `tests/unit/test_langsmith_settings_validator.py`

**Design:** Per spec §6: 3 new Settings fields backed by env vars. The fields are informational — langchain-core consumes the env vars directly. Settings exposes them so the startup validator can emit a warning if `LANGCHAIN_TRACING_V2=true` but `LANGSMITH_API_KEY` is unset (langchain would silently fail).

- [ ] **Step 1: Add fields to `Settings`**

Open `src/ai_sdr/settings.py`. Add inside the `Settings` class:

```python
    # LangSmith tracing — opt-in. langchain-core reads env vars directly;
    # these fields exist so main.py startup validator can warn on misconfig.
    langchain_tracing_v2: bool = False
    langsmith_api_key: str | None = None
    langchain_project: str = "pesdr-dev"
```

- [ ] **Step 2: Write the failing test for the validator**

Create `tests/unit/test_langsmith_settings_validator.py`:

```python
"""validate_langsmith_config — warn when tracing enabled without API key."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from ai_sdr.main import _validate_langsmith_config


def _settings(*, tracing=False, api_key=None, project="pesdr-dev"):
    s = MagicMock()
    s.langchain_tracing_v2 = tracing
    s.langsmith_api_key = api_key
    s.langchain_project = project
    return s


def test_passes_when_tracing_disabled() -> None:
    _validate_langsmith_config(_settings(tracing=False))  # no raise, no warn


def test_passes_when_tracing_enabled_with_api_key(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _validate_langsmith_config(_settings(tracing=True, api_key="ls__abc"))
    assert "LANGSMITH_API_KEY" not in caplog.text


def test_warns_when_tracing_enabled_without_api_key(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _validate_langsmith_config(_settings(tracing=True, api_key=None))
    assert "LANGSMITH_API_KEY" in caplog.text
    assert "silently" in caplog.text.lower() or "no-op" in caplog.text.lower()
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_langsmith_settings_validator.py -v`

Expected: FAIL — `_validate_langsmith_config` doesn't exist yet.

- [ ] **Step 4: Implement the validator + wire into lifespan**

Open `src/ai_sdr/main.py`. Add the helper above `lifespan`:

```python
def _validate_langsmith_config(settings) -> None:
    """Warn if LangSmith tracing is half-configured. Does NOT raise — the
    app boots either way; langchain just silently skips emitting traces
    if the API key is missing."""
    if not settings.langchain_tracing_v2:
        return
    if not settings.langsmith_api_key:
        import structlog
        structlog.get_logger().warning(
            "langsmith.misconfigured",
            reason=(
                "LANGCHAIN_TRACING_V2=true but LANGSMITH_API_KEY is unset — "
                "langchain will silently no-op tracing. Either unset "
                "LANGCHAIN_TRACING_V2 or provide a valid LANGSMITH_API_KEY "
                "(from https://smith.langchain.com → API Keys)."
            ),
            project=settings.langchain_project,
        )
```

In the `lifespan` function, after `configure_logging(...)` and BEFORE `await ensure_checkpointer_schema()`, add:

```python
    _validate_langsmith_config(settings)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_langsmith_settings_validator.py -v`

Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/settings.py src/ai_sdr/main.py tests/unit/test_langsmith_settings_validator.py
git commit -m "$(cat <<'EOF'
feat(plan10 t5): settings + startup validator for LangSmith config

3 new Settings fields (langchain_tracing_v2, langsmith_api_key,
langchain_project) — informational only; langchain-core consumes env
vars directly. Startup validator in main.py emits a structlog warning
when tracing is enabled without an API key (langchain would silently
no-op otherwise).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire `build_trace_metadata` into 4 LLM call sites

**Files:**
- Modify: `src/ai_sdr/treeflow/runtime.py:~234`
- Modify: `src/ai_sdr/treeflow/classifier.py:~84`
- Modify: `src/ai_sdr/llm/extractor.py:~82`
- Modify: `src/ai_sdr/guardrails/critic.py:~96`

**Design:** Per spec §6: each `ainvoke` call site gets a `config={"metadata": build_trace_metadata(...)}` kwarg with whatever context is locally available. trace_origin is the literal that identifies which site emitted the trace. Sub-traces inside `graph.ainvoke` (classifier/extractor/critic that run inside the LangGraph node) inherit parent metadata automatically — but each site still passes its own `trace_origin` so direct filtering in the dashboard works without depending on parent context.

These edits are **trivially independent** of each other — bundled into one task for cohesion.

- [ ] **Step 1: Edit `runtime.py`**

Open `src/ai_sdr/treeflow/runtime.py`. Find the `graph.ainvoke(input_state, config=cfg)` call (around line 234). The existing `cfg` is a `RunnableConfig` with `configurable.thread_id` only — we add `metadata`:

```python
from ai_sdr.observability.tracing import build_trace_metadata

# ... existing code up to where cfg is built ...

cfg: RunnableConfig = {
    "configurable": {"thread_id": talkflow.thread_id},
    "metadata": build_trace_metadata(
        tenant=tenant,
        talkflow=talkflow,
        lead=lead,  # may need to load — see step note below
        node=input_state.get("current_node"),
        turn_index=len(input_state.get("messages", [])),
        trace_origin="process_lead_inbox",
    ),
}
```

**Loading lead in this scope**: the runtime's `step()` method has `talkflow_id` but the `Lead` ORM row may not be loaded. Quick lookup:

```python
from ai_sdr.models.lead import Lead
lead = (
    await session.execute(select(Lead).where(Lead.id == talkflow.lead_id))
).scalar_one()
```

Add right before building `cfg`, after the `version` and `tf` loads that already exist in `step()`.

- [ ] **Step 2: Edit `classifier.py`**

Open `src/ai_sdr/treeflow/classifier.py`. Find `structured.ainvoke(messages)` (around line 84). Add metadata; lead/talkflow may not be in scope here — pass whatever the function receives. Modify signature if needed.

The current shape (sketch):
```python
async def classify(llm, objections, history, ...):
    ...
    structured = llm.with_structured_output(ClassifierResult)
    return cast(ClassifierResult, await structured.ainvoke(messages))
```

Threading context requires the caller (in `compiler.py` or wherever `classify` is invoked) to pass the metadata. Simpler approach: classifier accepts an optional `trace_metadata` arg that the caller fills:

```python
async def classify(
    llm,
    objections,
    history,
    *,
    trace_metadata: dict[str, Any] | None = None,
):
    ...
    structured = llm.with_structured_output(ClassifierResult)
    config = {"metadata": trace_metadata} if trace_metadata else {}
    return cast(ClassifierResult, await structured.ainvoke(messages, config=config))
```

And the caller (search for `classify(` in the codebase — likely in `treeflow/compiler.py`'s classifier node) passes:

```python
trace_metadata = build_trace_metadata(
    tenant=tenant, talkflow=talkflow, lead=lead,
    node=state.get("current_node"),
    trace_origin="objection_classifier",
)
result = await classify(llm, ..., trace_metadata=trace_metadata)
```

If `tenant/talkflow/lead` aren't in scope at the compiler classifier node, plumb them via `state` or a closure passed to `compile_treeflow`. The compiler already has `tenant_id` and `tenant_llm` — extend it to thread the full context.

Concrete edit: pass `tenant`/`talkflow`/`lead` through `compile_treeflow(..., tenant=tenant, talkflow=talkflow, lead=lead)` from `runtime.step()` and surface them in the classifier node closure. Mirror pattern for extractor + critic (see steps 3-4).

- [ ] **Step 3: Edit `extractor.py`**

Open `src/ai_sdr/llm/extractor.py`. Find `runnable.ainvoke(messages)` around line 82. Apply the same pattern — extract takes optional `trace_metadata`:

```python
async def extract_fields(
    llm, model, messages,
    *,
    trace_metadata: dict[str, Any] | None = None,
):
    runnable = llm.with_structured_output(model)
    config = {"metadata": trace_metadata} if trace_metadata else {}
    result = await runnable.ainvoke(messages, config=config)
    ...
```

Caller (in compiler.py extractor node) builds `trace_metadata` with `trace_origin="field_extractor"`.

- [ ] **Step 4: Edit `critic.py`**

Open `src/ai_sdr/guardrails/critic.py`. Find `runnable.ainvoke(messages)` around line 96. Same shape:

```python
async def critic_pass(
    llm,
    response,
    ...,
    *,
    trace_metadata: dict[str, Any] | None = None,
):
    ...
    runnable = llm.with_structured_output(Verdict)
    config = {"metadata": trace_metadata} if trace_metadata else {}
    result: Verdict = await runnable.ainvoke(messages, config=config)
```

Caller (in `guardrails/runner.py` where critic is invoked) builds metadata with `trace_origin="guardrails_critic"`.

- [ ] **Step 5: Smoke-import + unit run**

```bash
uv run python -c "from ai_sdr.treeflow.runtime import TalkFlowRuntime; from ai_sdr.treeflow.classifier import classify; from ai_sdr.llm.extractor import extract_fields; from ai_sdr.guardrails.critic import critic_pass; print('ok')"
```

Expected: prints `ok`. No import errors.

Run unit suite:
```bash
uv run pytest tests/unit/ -q
```

Expected: all green. Some tests that mock `ainvoke` may need a small update if they assert call_args without considering the new `config` kwarg — check by running.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/treeflow/runtime.py src/ai_sdr/treeflow/classifier.py src/ai_sdr/treeflow/compiler.py src/ai_sdr/llm/extractor.py src/ai_sdr/guardrails/critic.py src/ai_sdr/guardrails/runner.py
git commit -m "$(cat <<'EOF'
feat(plan10 t6): wire build_trace_metadata into 4 LLM call sites

runtime.graph.ainvoke gets metadata via existing cfg dict (trace_origin
process_lead_inbox). classifier/extractor/critic accept optional
trace_metadata kwarg threaded from the compiler nodes. Sub-traces
inherit parent metadata automatically; the explicit trace_origin per
site enables direct dashboard filtering without parent context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire outbound audit to `process_lead_inbox.send_text` paths

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py`
- Create: `tests/integration/test_outbound_audit_writes_from_inbound.py`
- Create: `tests/integration/test_outbound_audit_writes_from_send_failure.py`

**Design:** Per spec §7 + plan §4.1: in `process_lead_inbox._process_one`, after `adapter.send_text` returns success, call `record_outbound_sent`. In each `except` branch (`RecipientUnreachable`, `WindowExpiredError`, `AuthError`, `PolicyError`, generic `MessagingError`), call `record_outbound_failed` before the `await db.commit() + return`.

WindowExpiredError's recovery branch (with template fallback) is handled in **Task 8** — this task only adds the audit for the failure-mode entry into that branch.

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_outbound_audit_writes_from_inbound.py`:

```python
"""Worker inbound: send_text success → outbound_messages row with triggered_by=inbound."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def _seed(db_session) -> tuple[Tenant, TalkFlow, Lead, InboundMessageRow]:
    tenant = Tenant(slug=f"oi_{uuid.uuid4().hex[:6]}", display_name="OI")
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


async def test_send_text_success_writes_outbound_row(
    db_session, session_factory
) -> None:
    tenant, tf, lead, inbound = await _seed(db_session)

    adapter = FakeMessagingAdapter()
    runtime = MagicMock()
    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá! Como posso ajudar?")
    runtime.step = step_stub
    registry = MagicMock(); registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "sent"
    assert row.message_type == "text"
    assert row.body_text == "Olá! Como posso ajudar?"
    assert row.triggered_by == "inbound"
    assert row.inbound_message_id == inbound.id
    assert row.follow_up_job_id is None
    assert row.external_id  # populated by FakeMessagingAdapter
```

Create `tests/integration/test_outbound_audit_writes_from_send_failure.py`:

```python
"""Worker inbound: send_text failure → outbound_messages row with status=failed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_recipient_unreachable_writes_failed_outbound(
    db_session, session_factory
) -> None:
    # Same seed as previous test — abridged here
    tenant = Tenant(slug=f"f_{uuid.uuid4().hex[:6]}", display_name="F")
    db_session.add(tenant); await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0", content_hash="x"*64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv); await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead); await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf); await db_session.flush()
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound); await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(RecipientUnreachable("number not on WA"))

    runtime = MagicMock()
    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá")
    runtime.step = step_stub
    registry = MagicMock(); registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    row = (await db_session.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead.id)
    )).scalar_one()
    assert row.status == "failed"
    assert row.message_type == "text"
    assert row.body_text == "Olá"
    assert row.triggered_by == "inbound"
    assert "RecipientUnreachable" in (row.error_detail or "")
    assert row.external_id is None
```

- [ ] **Step 2: Modify `process_lead_inbox._process_one`**

Open `src/ai_sdr/worker/jobs/inbound.py`. Add imports at the top:

```python
from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)
```

Find `_process_one`. After the line that marks `msg.status = "processed"` + `msg.processed_at = datetime.now(UTC)` on send_text success (after `result = await adapter.send_text(...)`), insert:

```python
                    # P10: audit outbound (success)
                    # tenant_cfg is loaded here to read provider; if not already in scope,
                    # add: from ai_sdr.tenant_loader.loader import TenantLoader
                    #      tenant_cfg = TenantLoader(Path(get_settings().tenants_dir)).load(tenant.slug)
                    await record_outbound_sent(
                        db,
                        tenant=tenant,
                        talkflow=talkflow,
                        lead=lead,
                        provider=tenant_cfg.messaging.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        external_id=result.external_id,
                        sent_at=datetime.fromisoformat(result.sent_at_iso),
                        inbound_message_id=msg.id,
                    )
```

In each `except` branch (`RecipientUnreachable`, `AuthError`, `PolicyError`, generic `MessagingError`), BEFORE the existing `await db.commit() + return`, insert:

```python
                    await record_outbound_failed(
                        db,
                        tenant=tenant, talkflow=talkflow, lead=lead,
                        provider=tenant_cfg.messaging.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"{type(e).__name__}: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )
```

Apply to **4 except blocks**: `RecipientUnreachable`, `AuthError`, `PolicyError`, generic `MessagingError`. The `WindowExpiredError` branch is handled in Task 8 (recovery path).

- [ ] **Step 3: Run tests on VPS**

Controller: `git push -u origin dev/nicolas-p10` and `ssh vps-nova 'cd /root/PeSDR && git pull && uv run alembic upgrade head && uv run pytest tests/integration/test_outbound_audit_writes_from_inbound.py tests/integration/test_outbound_audit_writes_from_send_failure.py -v'`

Expected: both tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_outbound_audit_writes_from_inbound.py tests/integration/test_outbound_audit_writes_from_send_failure.py
git commit -m "$(cat <<'EOF'
feat(plan10 t7): audit outbound on send_text success + 4 failure paths

process_lead_inbox._process_one now writes an OutboundMessage row
after every adapter.send_text call (success: status='sent' + external_id;
failure in RecipientUnreachable / AuthError / PolicyError / generic
MessagingError: status='failed' + error_detail). triggered_by='inbound'
on all 5 paths; inbound_message_id links to the source row.

WindowExpiredError recovery audit lands in Task 8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire outbound audit to WindowExpired recovery path

**Files:**
- Modify: `src/ai_sdr/worker/jobs/inbound.py`
- Create: `tests/integration/test_outbound_audit_writes_from_window_expired_recovery.py`

**Design:** Per spec §7 + plan §4.2: in the `except WindowExpiredError` branch, the P9 recovery path calls `adapter.send_template(reengagement_template, ...)`. P10 adds audit on both inner outcomes (success → `record_outbound_sent` with `message_type='template'` + `triggered_by='window_expired_recovery'`; failure → `record_outbound_failed`). If no reengagement template configured, the existing P9 fallback (mark error, log warning) needs an audit too — write a `record_outbound_failed` with `message_type='text'` (the original send_text attempt) and `error_detail='window_expired_no_template'`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_outbound_audit_writes_from_window_expired_recovery.py`:

```python
"""WindowExpiredError + reengagement_template configured → 2 outbound rows
(1 failed text + 1 success template)."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import WindowExpiredError
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

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
    params: ["amigo"]
"""


@pytest.fixture
def isolated_tenants_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_window_expired_writes_failed_text_and_sent_template(
    db_session, isolated_tenants_dir, session_factory, monkeypatch
) -> None:
    from ai_sdr.settings import get_settings as _gs
    monkeypatch.setattr(_gs(), "tenants_dir", str(isolated_tenants_dir))

    tenant = Tenant(slug=f"wer_{uuid.uuid4().hex[:6]}", display_name="WER")
    db_session.add(tenant); await db_session.flush()

    (isolated_tenants_dir / tenant.slug).mkdir()
    (isolated_tenants_dir / tenant.slug / "tenant.yaml").write_text(
        _tenant_yaml_with_reengagement(tenant.slug)
    )

    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0", content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv); await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead); await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf); await db_session.flush()
    inbound = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"wamid_{uuid.uuid4().hex}", lead_id=lead.id,
        from_address="+5511999", text="oi",
        received_at=datetime.now(UTC), raw={},
    )
    db_session.add(inbound); await db_session.commit()

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(WindowExpiredError("24h expired"))

    runtime = MagicMock()
    async def step_stub(*a, **kw):
        return MagicMock(response_text="Olá")
    runtime.step = step_stub
    registry = MagicMock(); registry.get.return_value = adapter

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage)
        .where(OutboundMessage.lead_id == lead.id)
        .order_by(OutboundMessage.sent_at.asc())
    )).scalars().all()

    # Expect 2 rows: the failed text + the successful template recovery
    assert len(rows) == 2

    text_row = next(r for r in rows if r.message_type == "text")
    template_row = next(r for r in rows if r.message_type == "template")

    assert text_row.status == "failed"
    assert text_row.body_text == "Olá"
    assert text_row.triggered_by == "inbound"
    assert "WindowExpired" in (text_row.error_detail or "")

    assert template_row.status == "sent"
    assert template_row.template_ref == "reengagement_v1"
    assert template_row.template_language == "pt_BR"
    assert template_row.template_params == ["amigo"]
    assert template_row.triggered_by == "window_expired_recovery"
    assert template_row.inbound_message_id == inbound.id
```

- [ ] **Step 2: Modify the WindowExpired branch**

Open `src/ai_sdr/worker/jobs/inbound.py`. Find `except WindowExpiredError as e:`. The P9 task 12 already added the recovery logic. P10 adds audit on the 3 inner outcomes:

```python
                except WindowExpiredError as e:
                    # P10: audit the failed text send first
                    await record_outbound_failed(
                        db,
                        tenant=tenant, talkflow=talkflow, lead=lead,
                        provider=tenant_cfg.messaging.provider,
                        message_type="text",
                        triggered_by="inbound",
                        body_text=reply_text,
                        error_detail=f"WindowExpiredError: {e}",
                        sent_at=datetime.now(UTC),
                        inbound_message_id=msg.id,
                    )

                    # P9: try the reengagement template fallback
                    reeng = tenant_cfg.messaging.reengagement_template
                    if reeng is not None:
                        try:
                            from ai_sdr.follow_up.jinja import render_params
                            params = render_params(
                                reeng.params, lead=lead, tenant=tenant, collected={}
                            )
                            template_result = await adapter.send_template(
                                to=msg.from_address,
                                template_ref=reeng.template_ref,
                                language=reeng.language,
                                params=params,
                            )
                            msg.status = "processed"
                            msg.processed_at = datetime.now(UTC)
                            msg.error_detail = (
                                "window_expired; recovered via reengagement template"
                            )
                            talkflow.last_agent_message_at = datetime.now(UTC)
                            log.info(
                                "messaging.window_expired_recovered",
                                lead_id=str(lead.id),
                            )
                            # P10: audit the successful template send
                            await record_outbound_sent(
                                db,
                                tenant=tenant, talkflow=talkflow, lead=lead,
                                provider=tenant_cfg.messaging.provider,
                                message_type="template",
                                triggered_by="window_expired_recovery",
                                template_ref=reeng.template_ref,
                                template_language=reeng.language,
                                template_params=params,
                                external_id=template_result.external_id,
                                sent_at=datetime.fromisoformat(template_result.sent_at_iso),
                                inbound_message_id=msg.id,
                            )
                        except Exception as e2:
                            msg.status = "error"
                            msg.error_detail = f"window_expired; reengagement failed: {e2}"
                            log.warning(
                                "messaging.reengagement_failed",
                                lead_id=str(lead.id), err=str(e2),
                            )
                            # P10: audit the failed template send
                            await record_outbound_failed(
                                db,
                                tenant=tenant, talkflow=talkflow, lead=lead,
                                provider=tenant_cfg.messaging.provider,
                                message_type="template",
                                triggered_by="window_expired_recovery",
                                template_ref=reeng.template_ref,
                                template_language=reeng.language,
                                template_params=params,
                                error_detail=f"reengagement_failed: {e2}",
                                sent_at=datetime.now(UTC),
                                inbound_message_id=msg.id,
                            )
                    else:
                        msg.status = "error"
                        msg.error_detail = f"window_expired: {e}"
                        log.warning(
                            "messaging.window_expired_no_template",
                            lead_id=str(lead.id),
                        )
                        # No second audit row — the original text failure
                        # already covers the window_expired_no_template case.
                    await db.commit()
                    return
```

- [ ] **Step 3: Run on VPS**

```bash
ssh vps-nova 'cd /root/PeSDR && git pull && uv run pytest tests/integration/test_outbound_audit_writes_from_window_expired_recovery.py -v'
```

Expected: test PASSES.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_outbound_audit_writes_from_window_expired_recovery.py
git commit -m "$(cat <<'EOF'
feat(plan10 t8): audit outbound on WindowExpired recovery path

WindowExpiredError now emits 2 audit rows when reengagement template
is configured: (1) the failed text send (triggered_by='inbound',
message_type='text', status='failed'), (2) the template send
(triggered_by='window_expired_recovery', message_type='template',
status='sent' or 'failed'). When no template configured, only the
failed text row exists (no second audit needed).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Wire outbound audit to `follow_up_scanner._fire_follow_up`

**Files:**
- Modify: `src/ai_sdr/worker/jobs/follow_up_scanner.py`
- Create: `tests/integration/test_outbound_audit_writes_from_follow_up_scanner.py`

**Design:** Per spec §7 + plan §4.3: scanner success path calls `record_outbound_sent` with `triggered_by='follow_up_scanner'` and `follow_up_job_id=job.id`. Each failure `except` branch (`RecipientUnreachable`, `AuthError/PolicyError/MessagingError`) calls `record_outbound_failed`.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_outbound_audit_writes_from_follow_up_scanner.py`:

```python
"""Scanner fires job → 1 outbound row with triggered_by=follow_up_scanner."""

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
from ai_sdr.models.outbound_message import OutboundMessage
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
      template_ref: followup_24h_v1
      language: pt_BR
      params: ["{{ collected.nome | default('amigo') }}"]
"""


@pytest.fixture
def session_factory():
    return async_sessionmaker(build_engine(get_settings().database_url), expire_on_commit=False)


async def test_scanner_send_template_writes_outbound_row(
    db_session, session_factory
) -> None:
    tenant = Tenant(slug=f"sa_{uuid.uuid4().hex[:6]}", display_name="SA")
    db_session.add(tenant); await db_session.flush()
    await set_tenant_context(db_session, tenant.id)

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64, content_yaml=_YAML,
    )
    db_session.add(tv); await db_session.flush()

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="active")
    db_session.add(lead); await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
        last_agent_message_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(tf); await db_session.flush()

    job = FollowUpJob(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        attempt_number=1,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(job); await db_session.commit()

    adapter = FakeMessagingAdapter()
    registry = MagicMock(); registry.get.return_value = adapter

    await follow_up_scanner({
        "session_factory": session_factory,
        "adapter_registry": registry,
    })

    await set_tenant_context(db_session, tenant.id)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(OutboundMessage).where(OutboundMessage.lead_id == lead.id)
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "sent"
    assert row.message_type == "template"
    assert row.template_ref == "followup_24h_v1"
    assert row.template_params == ["amigo"]
    assert row.triggered_by == "follow_up_scanner"
    assert row.follow_up_job_id == job.id
    assert row.inbound_message_id is None
```

- [ ] **Step 2: Modify `_fire_follow_up`**

Open `src/ai_sdr/worker/jobs/follow_up_scanner.py`. Add import at the top:

```python
from ai_sdr.observability.outbound_audit import (
    record_outbound_failed,
    record_outbound_sent,
)
```

In `_fire_follow_up`, locate the success path (after `result = await adapter.send_template(...)` and before `job.status = "completed"`). Insert:

```python
            # P10: audit the successful template send
            await record_outbound_sent(
                db,
                tenant=tenant,
                talkflow=talkflow,
                lead=lead,
                provider="whatsapp_cloud",  # or read from tenant_cfg if registry has it
                message_type="template",
                triggered_by="follow_up_scanner",
                template_ref=step.template_ref,
                template_language=step.language,
                template_params=params,
                external_id=result.external_id,
                sent_at=datetime.fromisoformat(result.sent_at_iso),
                follow_up_job_id=job.id,
            )
```

In each `except` branch (`RecipientUnreachable`, `(AuthError, PolicyError, MessagingError)`), BEFORE the existing `await db.commit()`, insert:

```python
            await record_outbound_failed(
                db,
                tenant=tenant, talkflow=talkflow, lead=lead,
                provider="whatsapp_cloud",
                message_type="template",
                triggered_by="follow_up_scanner",
                template_ref=step.template_ref,
                template_language=step.language,
                template_params=params,
                error_detail=f"{type(e).__name__}: {e}",
                sent_at=datetime.now(UTC),
                follow_up_job_id=job.id,
            )
```

**Note on `provider="whatsapp_cloud"`**: scanner doesn't load `tenant_cfg` today. Either (a) load it inline (`TenantLoader(Path(get_settings().tenants_dir)).load(tenant.slug)`), or (b) hardcode `"whatsapp_cloud"` since that's the only adapter that supports templates in v1. **Recommendation: hardcode** for simplicity. Vialum adapter (when it lands) will require loading tenant_cfg here — that's a future refactor.

- [ ] **Step 3: Run on VPS**

```bash
ssh vps-nova 'cd /root/PeSDR && git pull && uv run pytest tests/integration/test_outbound_audit_writes_from_follow_up_scanner.py -v'
```

Expected: test PASSES.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/worker/jobs/follow_up_scanner.py tests/integration/test_outbound_audit_writes_from_follow_up_scanner.py
git commit -m "$(cat <<'EOF'
feat(plan10 t9): audit outbound on follow_up_scanner send_template paths

Scanner success path writes OutboundMessage with triggered_by=
'follow_up_scanner', follow_up_job_id linking back to the job. Each
failure branch (RecipientUnreachable, AuthError/PolicyError/
MessagingError) writes status='failed' with the typed error_detail.

provider hardcoded to 'whatsapp_cloud' for v1 since templates only
work through that adapter. Vialum adapter (future) will refactor to
read from tenant_cfg.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `ai-sdr outbound list` CLI

**Files:**
- Create: `src/ai_sdr/cli/outbound.py`
- Modify: `src/ai_sdr/cli/app.py`
- Create: `tests/unit/test_outbound_cli.py`
- Create: `tests/integration/test_outbound_cli_integration.py`

**Design:** Per spec §8: one command, `ai-sdr outbound list`, with 3 filters and a `--limit`. Tabular output via rich, ordered by `sent_at DESC`. Same session pattern as other CLIs (open own engine).

- [ ] **Step 1: Write the failing unit test**

Create `tests/unit/test_outbound_cli.py`:

```python
"""ai-sdr outbound list — typer wiring + filter argument parsing."""

from __future__ import annotations

from typer.testing import CliRunner

from ai_sdr.cli.app import app

runner = CliRunner()


def test_outbound_list_help_includes_filters() -> None:
    r = runner.invoke(app, ["outbound", "list", "--help"])
    assert r.exit_code == 0
    assert "--tenant" in r.output
    assert "--lead" in r.output
    assert "--status" in r.output
    assert "--limit" in r.output


def test_outbound_list_requires_tenant() -> None:
    r = runner.invoke(app, ["outbound", "list"])
    assert r.exit_code != 0
    assert "tenant" in r.output.lower()
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_outbound_cli.py -v`

Expected: FAIL — `outbound` subcommand not registered.

- [ ] **Step 3: Implement the CLI**

Create `src/ai_sdr/cli/outbound.py`:

```python
"""ai-sdr outbound — query the outbound_messages audit table."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.tenant import Tenant
from ai_sdr.settings import get_settings

outbound_app = typer.Typer(help="Outbound messages audit query")
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


def _truncate(s: str | None, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


@outbound_app.command("list")
def list_(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug (required)")],
    lead: Annotated[
        str | None, typer.Option("--lead", help="Filter by lead UUID")
    ] = None,
    status: Annotated[
        str,
        typer.Option("--status", help="Filter: sent | failed | all (default all)"),
    ] = "all",
    limit: Annotated[
        int, typer.Option("--limit", help="Max rows to display (default 50)")
    ] = 50,
) -> None:
    """List outbound messages for a tenant, ordered by most recent first."""
    asyncio.run(_list_async(tenant, lead, status, limit))


async def _list_async(
    tenant_slug: str, lead_filter: str | None, status_filter: str, limit: int
) -> None:
    sm, engine = _make_session()
    async with sm() as session:
        t = await _load_tenant(session, tenant_slug)
        await set_tenant_context(session, t.id)
        stmt = select(OutboundMessage).order_by(OutboundMessage.sent_at.desc())
        if status_filter != "all":
            if status_filter not in ("sent", "failed"):
                console.print(
                    f"[red]invalid --status: {status_filter!r} (use sent|failed|all)[/red]"
                )
                raise typer.Exit(1)
            stmt = stmt.where(OutboundMessage.status == status_filter)
        if lead_filter:
            stmt = stmt.where(OutboundMessage.lead_id == uuid.UUID(lead_filter))
        stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            console.print(f"[yellow]no outbound messages (status={status_filter!r})[/yellow]")
            await engine.dispose()
            return

        table = Table(title=f"Outbound — {tenant_slug} ({status_filter}, last {limit})")
        table.add_column("Sent At", no_wrap=True)
        table.add_column("Type")
        table.add_column("Lead", no_wrap=True)
        table.add_column("Trigger", no_wrap=True)
        table.add_column("Status")
        table.add_column("Content / Template")
        table.add_column("External ID", no_wrap=True)
        for r in rows:
            content = (
                _truncate(r.body_text, 40)
                if r.message_type == "text"
                else f"{r.template_ref} {r.template_params or []}"
            )
            content = _truncate(content, 60)
            if r.status == "failed":
                content = f"{content} :: {_truncate(r.error_detail, 30)}"
            table.add_row(
                r.sent_at.strftime("%Y-%m-%d %H:%M:%S"),
                r.message_type,
                str(r.lead_id)[:8] + "…",
                r.triggered_by,
                ("[green]sent[/green]" if r.status == "sent" else "[red]failed[/red]"),
                content,
                _truncate(r.external_id, 18),
            )
        console.print(table)
    await engine.dispose()
```

- [ ] **Step 4: Register the sub-app**

Open `src/ai_sdr/cli/app.py`. Add:

```python
from ai_sdr.cli.outbound import outbound_app
# ...
app.add_typer(outbound_app, name="outbound")
```

- [ ] **Step 5: Run unit tests**

```bash
uv run pytest tests/unit/test_outbound_cli.py -v
```

Expected: both PASS.

- [ ] **Step 6: Write integration test**

Create `tests/integration/test_outbound_cli_integration.py`:

```python
"""ai-sdr outbound list — hits real DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from ai_sdr.cli.app import app
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.outbound_message import OutboundMessage
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration

runner = CliRunner()


async def test_list_shows_recent_outbound(db_session) -> None:
    tenant = Tenant(slug=f"cli_{uuid.uuid4().hex[:6]}", display_name="CLI")
    db_session.add(tenant); await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0", content_hash="x"*64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv); await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="active")
    db_session.add(lead); await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf); await db_session.flush()

    # 1 sent text + 1 failed template
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud", message_type="text", body_text="Olá",
        status="sent", external_id="wamid.A", triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud", message_type="template",
        template_ref="t1", template_language="pt_BR", template_params=["x"],
        status="failed", error_detail="AuthError: bad token",
        triggered_by="follow_up_scanner",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    r = runner.invoke(app, ["outbound", "list", "--tenant", tenant.slug])
    assert r.exit_code == 0
    assert "Olá" in r.output
    assert "t1" in r.output
    assert "inbound" in r.output
    assert "follow_up_scanner" in r.output
    assert "sent" in r.output
    assert "failed" in r.output


async def test_list_filter_status_failed(db_session) -> None:
    tenant = Tenant(slug=f"cli2_{uuid.uuid4().hex[:6]}", display_name="C2")
    db_session.add(tenant); await db_session.flush()
    await set_tenant_context(db_session, tenant.id)
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0", content_hash="x"*64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv); await db_session.flush()
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+2", status="active")
    db_session.add(lead); await db_session.flush()
    tf = TalkFlow(
        tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf); await db_session.flush()
    db_session.add(OutboundMessage(
        tenant_id=tenant.id, talkflow_id=tf.id, lead_id=lead.id,
        provider="whatsapp_cloud", message_type="text", body_text="OK",
        status="sent", external_id="wamid.X", triggered_by="inbound",
        sent_at=datetime.now(UTC),
    ))
    await db_session.commit()

    r = runner.invoke(app, ["outbound", "list", "--tenant", tenant.slug, "--status", "failed"])
    assert r.exit_code == 0
    assert "no outbound messages" in r.output.lower()
```

- [ ] **Step 7: Push + run integration on VPS**

```bash
ssh vps-nova 'cd /root/PeSDR && git pull && uv run pytest tests/integration/test_outbound_cli_integration.py -v'
```

Expected: both tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/ai_sdr/cli/outbound.py src/ai_sdr/cli/app.py tests/unit/test_outbound_cli.py tests/integration/test_outbound_cli_integration.py
git commit -m "$(cat <<'EOF'
feat(plan10 t10): ai-sdr outbound list CLI

Single command with --tenant (required), --lead (optional UUID),
--status (sent|failed|all, default all), --limit (default 50).
Rich table output ordered by sent_at DESC. Truncation on long
content/IDs for readability. Failed rows include error_detail inline
with the content cell.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Wiring — docker-compose env vars + .env.example + CLAUDE.md

**Files:**
- Modify: `docker-compose.yml`
- Create (or modify if exists): `.env.example`
- Modify: `CLAUDE.md`

**Design:** Per spec §6 + §9: API and worker services need the 3 LangSmith env vars passed through from the host. `.env.example` documents them as commented opt-ins. `CLAUDE.md` gets a "Observability (Plano 10)" section with setup steps + filter examples for the dashboard.

- [ ] **Step 1: Edit `docker-compose.yml`**

Open `docker-compose.yml`. Find the `api:` service `environment:` block. Add (preserving existing entries):

```yaml
      LANGCHAIN_TRACING_V2: ${LANGCHAIN_TRACING_V2:-false}
      LANGSMITH_API_KEY: ${LANGSMITH_API_KEY:-}
      LANGCHAIN_PROJECT: ${LANGCHAIN_PROJECT:-pesdr-dev}
```

Do the same for the `worker:` service. Both consume traces during their respective LLM calls (API never makes LLM calls today, but the configuration is identical for consistency and future-proofing).

- [ ] **Step 2: Create/modify `.env.example`**

If `.env.example` doesn't exist, create it. Add (or merge with existing content):

```bash
# LangSmith tracing — optional. When LANGCHAIN_TRACING_V2=true and
# LANGSMITH_API_KEY is set, all LLM calls are traced to LangSmith
# automatically (no code change needed). Get an API key at:
#   https://smith.langchain.com → API Keys
#
# Leave these unset to disable tracing entirely (default).
# LANGCHAIN_TRACING_V2=true
# LANGSMITH_API_KEY=ls__your-key-here
# LANGCHAIN_PROJECT=pesdr-dev
```

- [ ] **Step 3: Update `CLAUDE.md`**

Open `CLAUDE.md`. Append a new section at the end:

````markdown
## Observability (Plano 10)

### LangSmith tracing

Opt-in via 3 env vars (in `.env`, or in the VPS environment):

```bash
LANGCHAIN_TRACING_V2=true
LANGSMITH_API_KEY=ls__...                  # from https://smith.langchain.com
LANGCHAIN_PROJECT=pesdr-prod                # or pesdr-dev locally
```

When set, langchain-core auto-traces every chain run from the 4 LLM call sites:
- `runtime.graph.ainvoke` (trace_origin=`process_lead_inbox`)
- `classifier.structured.ainvoke` (trace_origin=`objection_classifier`)
- `extractor.runnable.ainvoke` (trace_origin=`field_extractor`)
- `critic.runnable.ainvoke` (trace_origin=`guardrails_critic`)

Each trace carries metadata: `{tenant_id, tenant_slug, talkflow_id, lead_id, node, turn_index, trace_origin}`.

**Filter examples in the LangSmith dashboard:**
- All traces for Joana: `metadata.tenant_slug = "joana"`
- All critic passes: `metadata.trace_origin = "guardrails_critic"`
- Traces for a specific lead: `metadata.lead_id = "uuid"`
- Slow turns: `latency > 10s` + filter by metadata

**Without `LANGSMITH_API_KEY`** but with `LANGCHAIN_TRACING_V2=true`: langchain silently no-ops; the app boots a structlog warning at startup so the operator notices.

**Sampling:** 100% in v1. If volume exceeds free tier (5k traces/mo), add sampling via a future plan.

### Outbound audit (`outbound_messages` table)

Every adapter send is persisted with full context — `body_text` or `template_ref + template_params`, `status` (sent/failed), `error_detail`, `triggered_by` (inbound | follow_up_scanner | window_expired_recovery), and FKs to the source `inbound_message` or `follow_up_job`.

Query via CLI:
```bash
ai-sdr outbound list --tenant <slug>                              # last 50, all statuses
ai-sdr outbound list --tenant <slug> --status failed              # only failures
ai-sdr outbound list --tenant <slug> --lead <uuid>                # history of one lead
ai-sdr outbound list --tenant <slug> --status sent --limit 200    # more rows
```

Or directly in `psql`:
```sql
SELECT sent_at, message_type, status, body_text, template_ref, error_detail, triggered_by
FROM outbound_messages
WHERE tenant_id = '<uuid>' AND lead_id = '<uuid>'
ORDER BY sent_at DESC LIMIT 50;
```

(Both methods require `set_tenant_context()` to be set if hitting via the app role; `psql` as superuser bypasses RLS.)

### Known race

When `adapter.send_*` succeeds but the worker's `db.commit()` then fails (DB hiccup, etc.), the message went out to Meta but the audit row is lost. The worker emits `log.warning("outbound.audit_lost", external_id=..., ...)` with enough payload to reconstruct manually. No automatic retry — Meta's `external_id` can't be duplicated without double-sending. 2-phase outbox pattern is a future plan if this becomes operational pain.

### What's NOT here

- Prometheus / Grafana / OTel — defer until volume justifies (multi-customer scale).
- Alerts / paging — log structured serves; alert routing is a future plan.
- Cost dashboard custom — LangSmith UI already reports tokens + cost per provider.
- Trace of DB queries / arq jobs — only LLM calls are traced. Add via `@traceable` decorator in a future plan if needed.

````

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example CLAUDE.md
git commit -m "$(cat <<'EOF'
feat(plan10 t11): docker-compose env passthrough + .env.example + CLAUDE.md

API + worker services get the 3 LangSmith env vars passed from host
(default to safe values). .env.example documents the opt-in shape.
CLAUDE.md gets the "Observability (Plano 10)" section with setup,
dashboard filter examples, CLI usage, race notes, and explicit
out-of-scope items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Live LangSmith integration test (opt-in, gated)

**Files:**
- Create: `tests/integration/test_langsmith_live.py`

**Design:** Per spec §10: gated by `pytest.mark.live_llm` + a valid `LANGSMITH_API_KEY`. Makes one trivial LLM call with metadata, waits briefly, then GETs the LangSmith API to confirm the trace arrived with the expected metadata. Skip cleanly when env unconfigured (no failure noise).

- [ ] **Step 1: Write the test**

Create `tests/integration/test_langsmith_live.py`:

```python
"""End-to-end LangSmith live test. Opt-in via LIVE_LANGSMITH=1 env var.

Sends a trivial LLM call with build_trace_metadata, polls the LangSmith
API for the trace, asserts metadata fields. Skipped by default to keep
the suite hermetic.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.live_llm]


def _skip_if_unconfigured() -> str:
    if os.getenv("LIVE_LANGSMITH") != "1":
        pytest.skip("LIVE_LANGSMITH=1 not set; live LangSmith test is opt-in")
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        pytest.skip("LANGSMITH_API_KEY not set")
    if os.getenv("LANGCHAIN_TRACING_V2") != "true":
        pytest.skip("LANGCHAIN_TRACING_V2=true required for live test")
    project = os.getenv("LANGCHAIN_PROJECT", "pesdr-dev")
    return project


async def test_trace_arrives_with_metadata() -> None:
    project = _skip_if_unconfigured()

    # Use Anthropic Haiku for a tiny ping — cheap and fast.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY required for the live LLM ping")

    from langchain_anthropic import ChatAnthropic

    from ai_sdr.observability.tracing import build_trace_metadata

    test_marker = f"langsmith-live-test-{uuid.uuid4().hex[:8]}"

    llm = ChatAnthropic(model="claude-haiku-4-5", api_key=api_key, max_tokens=20)
    metadata = build_trace_metadata(trace_origin="simulate")
    metadata["test_marker"] = test_marker  # we look this up in the API

    await llm.ainvoke(
        [{"role": "user", "content": f"Reply with the single word: pong ({test_marker})"}],
        config={"metadata": metadata},
    )

    # Wait a bit for LangSmith to ingest.
    await asyncio.sleep(3)

    # Poll the API for the trace.
    ls_api_key = os.getenv("LANGSMITH_API_KEY")
    async with httpx.AsyncClient(
        base_url="https://api.smith.langchain.com",
        headers={"X-API-Key": ls_api_key, "Content-Type": "application/json"},
        timeout=15.0,
    ) as client:
        # Find runs in the project filtered by our test_marker metadata.
        # LangSmith's runs.query endpoint accepts metadata filters.
        for attempt in range(5):
            r = await client.post(
                "/runs/query",
                json={
                    "project_name": project,
                    "filter": f'eq(metadata.test_marker, "{test_marker}")',
                    "limit": 5,
                },
            )
            if r.status_code == 200 and r.json().get("runs"):
                runs = r.json()["runs"]
                first = runs[0]
                # Confirm metadata roundtripped
                run_meta = first.get("extra", {}).get("metadata", {})
                assert run_meta.get("test_marker") == test_marker
                assert run_meta.get("trace_origin") == "simulate"
                return
            await asyncio.sleep(2)

        pytest.fail(
            f"trace with test_marker={test_marker} did not appear in LangSmith "
            f"project={project} within ~10s"
        )
```

- [ ] **Step 2: Skip by default**

The test is gated by 3 conditions (`LIVE_LANGSMITH=1`, `LANGSMITH_API_KEY`, `LANGCHAIN_TRACING_V2=true`) plus the `live_llm` marker. Default suite runs skip it cleanly.

- [ ] **Step 3: Optional manual run by the operator**

```bash
LIVE_LANGSMITH=1 \
LANGCHAIN_TRACING_V2=true \
LANGSMITH_API_KEY=ls__... \
LANGCHAIN_PROJECT=pesdr-dev \
ANTHROPIC_API_KEY=sk-ant-... \
uv run pytest tests/integration/test_langsmith_live.py -v -m live_llm
```

Expected: passes if the LangSmith API confirms the trace within ~15s.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_langsmith_live.py
git commit -m "$(cat <<'EOF'
test(plan10 t12): live LangSmith end-to-end (opt-in, gated)

Triple-gated: pytest.mark.live_llm + LIVE_LANGSMITH=1 +
LANGCHAIN_TRACING_V2=true + LANGSMITH_API_KEY + ANTHROPIC_API_KEY.
Skips cleanly when unconfigured.

Makes a 1-token ping via Anthropic Haiku with a unique test_marker
in metadata, then polls api.smith.langchain.com/runs/query until the
trace appears. Confirms metadata roundtripped (trace_origin +
test_marker).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Close-out

**Files:** None (close-out tasks).

- [ ] **Step 1: Run full unit suite locally**

```bash
make lint && make format && make type && make test-unit
```

Expected: all green.

- [ ] **Step 2: Push branch + validate integration on VPS**

```bash
git push -u origin dev/nicolas-p10

ssh vps-nova 'export PATH=/root/.local/bin:$PATH && cd /root/PeSDR && git fetch origin && git checkout dev/nicolas-p10 && uv sync && uv run alembic upgrade head && uv run pytest tests/integration -q'
```

Expected: integration suite green (known noise: 6 P5 flakes + 5 P4a auth-skipped). No NEW failures.

- [ ] **Step 3: Smoke the API + worker boot with tracing flag**

On the VPS, temporarily set `LANGCHAIN_TRACING_V2=true` without an API key in `.env`, then:

```bash
docker compose up -d --build api worker
docker compose logs --tail=20 api | grep langsmith
```

Expected: see `langsmith.misconfigured` warning in the log (this confirms the validator works).

Reset `LANGCHAIN_TRACING_V2=false` and restart to silence.

- [ ] **Step 4: Tag the close-out commit**

```bash
git commit --allow-empty -m "$(cat <<'EOF'
chore(plan10): close-out — all 12 tasks landed

LangSmith tracing opt-in via env vars; 4 LLM call sites carry
structured metadata (tenant_slug, lead_id, talkflow_id, node,
turn_index, trace_origin). outbound_messages table persists every
adapter send (success and failure) with causal FK to inbound or
follow_up_job. ai-sdr outbound list CLI for ops audit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Notes for plan execution

- **Migration 0011 depends on 0010** (`outbound_messages.follow_up_job_id` references `follow_up_jobs.id`). When merging to trunk, ensure P9 is merged first so `dev/nicolas` already has 0010. If P9 hasn't merged when P10 runs, the migration coordinator handles ordering at the merge step.
- **Task 6 ripples** — 4 LLM call sites get metadata, which means classifier/extractor/critic also need their callers (in `treeflow/compiler.py` and `guardrails/runner.py`) to **pass tenant/talkflow/lead context**. If `compile_treeflow` doesn't already receive those, the signature grows. Audit the call graph from `runtime.step()` outward before editing — keep the signature change minimal (one new optional kwarg per function).
- **Tasks 7-9 add 10 audit write sites** (5 in worker inbound success + failure paths, 2 in WindowExpired recovery, 3 in scanner failure paths + 1 success). All use the same 2 helpers from Task 4. Read the file shapes from P9 before editing — they were already modified by P9 tasks 11-13.
- **The `tenant_cfg` lookup** in Task 9 scanner is the spot to either load inline or hardcode `"whatsapp_cloud"`. Pick hardcode for v1 simplicity; Vialum adapter will refactor.
- **Live test (Task 12)** is intentionally gated triple — it costs a few cents per run AND requires a live API key. Default CI runs skip; operator runs manually before declaring observability rollout complete.
