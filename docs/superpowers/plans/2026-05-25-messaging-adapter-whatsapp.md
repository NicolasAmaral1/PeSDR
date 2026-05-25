# Messaging Adapter + WhatsApp Cloud Implementation Plan (Plano 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take PeSDR out of the terminal. Introduce a `MessagingAdapter` abstraction + `WhatsAppCloudAPIAdapter` default impl, FastAPI webhook routes (`/webhooks/{tenant_slug}/{provider}`), an arq worker that drains queued inbounds via `runtime.step()`, and the `leads` / `inbound_messages` tables that let an HITL operator assign a treeflow to a new lead via CLI or REST (`POST /tenants/{slug}/leads/{id}/assign`). After this plan, the example tenant can receive a real WhatsApp message, an operator assigns the funnel, and the agent replies — all via the existing TreeFlow + KB + Guardrails stack from Plans 1–3.

**Architecture:** First application of the standalone+Vialum adapter ADR. Adapter is pure (no DB, no lead/tenant model knowledge — speaks opaque `to: str`). Webhook handler is fast (verify + parse + dedupe insert + enqueue, never calls LLM). Worker is a separate process (`ai-sdr worker`) using arq + Redis; serialization per lead via Postgres `pg_advisory_lock` so different leads run in parallel but the same lead processes its queue in `received_at ASC` order. Errors from the provider come back as typed exceptions (`AuthError`, `RecipientUnreachable`, `WindowExpiredError`, `PolicyError`) — the worker maps each to a concrete action (mark lead unreachable, log+alert, hook for Plano 9). Bootstrap is HITL-friendly: lead nasce `pending_assignment`; messages stay queued until operator assigns; on assignment the worker replays **all** accumulated inbounds in order.

**Tech Stack additions:** `arq>=0.26` (async Redis task queue — already use Redis), `tenacity>=8` (declarative retry policies for transient WhatsApp errors). `httpx>=0.28` is already a dep (used by `langchain-openai`). No new vendor SDKs — WhatsApp Cloud API is plain JSON over HTTPS.

**Spec:** [`docs/superpowers/specs/2026-05-24-messaging-adapter-design.md`](../specs/2026-05-24-messaging-adapter-design.md). Read §3 (non-goals), §5 (contract), §6 (data model), §7 (webhook), §8 (worker), §9 (WhatsApp specifics), §10 (lead assignment), §11 (testing), §14 (future hooks) before starting.

---

## File Structure

```
src/ai_sdr/
├── messaging/                              # NEW package — the adapter pattern's 1st boundary
│   ├── __init__.py                         # NEW (empty)
│   ├── base.py                             # NEW: InboundMessage, SendResult, MessagingAdapter ABC
│   ├── errors.py                           # NEW: MessagingError hierarchy
│   ├── factory.py                          # NEW: build_messaging_adapter(cfg, secrets) dispatch
│   ├── registry.py                         # NEW: AdapterRegistry singleton cache by (tenant_id, provider)
│   ├── fake.py                             # NEW: FakeMessagingAdapter (testing + simulator hook)
│   ├── ingest.py                           # NEW: find_or_create_lead_by_address + ingest_inbound_message
│   └── whatsapp_cloud.py                   # NEW: WhatsAppCloudAPIAdapter (default standalone impl)
│
├── worker/                                 # NEW package — arq worker process
│   ├── __init__.py                         # NEW (empty)
│   ├── main.py                             # NEW: WorkerSettings + arq_pool factory
│   └── jobs/
│       ├── __init__.py                     # NEW (empty)
│       └── inbound.py                      # NEW: process_lead_inbox(ctx, tenant_id, lead_id)
│
├── models/
│   ├── lead.py                             # NEW: Lead ORM
│   ├── inbound_message.py                  # NEW: InboundMessageRow ORM (note the *Row suffix —
│   │                                       #      avoids name collision with messaging.base.InboundMessage)
│   ├── talkflow.py                         # MODIFIED: lead_id String → UUID FK
│   └── __init__.py                         # MODIFIED: re-export Lead + InboundMessageRow
│
├── schemas/
│   └── tenant_yaml.py                      # MODIFIED: TenantConfig gains messaging: MessagingConfig | None
│
├── api/
│   ├── deps.py                             # MODIFIED: add arq_pool dep
│   └── routes/
│       ├── webhooks.py                     # NEW: GET challenge + POST ingest
│       └── leads.py                        # NEW: GET pending + POST assign
│
├── cli/
│   ├── app.py                              # MODIFIED: register `worker` + `leads` subcommands
│   ├── worker.py                           # NEW: typer command `ai-sdr worker` (arq runner)
│   ├── leads.py                            # NEW: typer commands `list-pending` + `assign-lead`
│   └── simulate.py                         # MODIFIED: find-or-create lead by external_label
│
├── treeflow/
│   └── runtime.py                          # MODIFIED: create() takes lead_id: UUID instead of str
│
└── main.py                                 # MODIFIED: include webhooks + leads routers

migrations/versions/
├── 0006_leads_table.py                     # NEW
├── 0007_inbound_messages_table.py          # NEW
└── 0008_talkflows_lead_id_fk.py            # NEW

tenants/example/
├── tenant.yaml                             # MODIFIED: adds messaging block (whatsapp_cloud provider)
└── secrets.enc.yaml                        # MODIFIED (on the VPS): adds wa_* keys

docker-compose.yml                          # MODIFIED: adds worker service
pyproject.toml                              # MODIFIED: adds arq + tenacity
CLAUDE.md                                   # MODIFIED: Plano 5 messaging section

tests/
├── unit/
│   ├── test_tenant_yaml.py                 # MODIFIED: MessagingConfig validation tests
│   ├── test_messaging_errors.py            # NEW
│   ├── test_messaging_base.py              # NEW (frozen dataclasses + ABC enforcement)
│   ├── test_messaging_fake.py              # NEW
│   ├── test_messaging_factory.py           # NEW
│   ├── test_messaging_registry.py          # NEW
│   ├── test_whatsapp_challenge.py          # NEW
│   ├── test_whatsapp_handle_inbound.py     # NEW (HMAC + parse with real captured fixtures)
│   ├── test_whatsapp_send_text.py          # NEW (httpx mocked, error classification table)
│   └── test_lead_model.py                  # NEW (Pydantic-side; ORM tests below)
│
├── integration/
│   ├── test_leads_rls.py                   # NEW
│   ├── test_inbound_messages_rls.py        # NEW
│   ├── test_talkflows_lead_fk.py           # NEW
│   ├── test_messaging_ingest.py            # NEW (find_or_create_lead + dedupe)
│   ├── test_webhook_routes.py              # NEW (challenge + ingest + signature)
│   ├── test_worker_process_lead_inbox.py   # NEW (advisory lock, replay-all, error taxonomy)
│   ├── test_leads_routes.py                # NEW (pending list + assign endpoint)
│   ├── test_adapter_compliance.py          # NEW (parametrized: fake + whatsapp_cloud_mocked)
│   └── test_messaging_e2e.py               # NEW (webhook → enqueue → worker → fake.send → assert)
│
└── fixtures/
    └── whatsapp/
        ├── inbound_text.json               # NEW (real-shaped payload)
        ├── inbound_status_update.json      # NEW (delivered/read — should be skipped)
        └── inbound_image.json              # NEW (non-text — should be skipped in Plano 5)
```

**Layout notes:**
- `messaging/` is the new boundary. `base.py` and `errors.py` are pure Python (no SQLAlchemy, no httpx) so they're cheap to import everywhere. `fake.py` is the test/simulator harness.
- `worker/` is a brand-new package. Keeping it separate from `messaging/` because the worker is a *consumer* of the adapter — confusing if they share a module.
- `models/inbound_message.py` is named `InboundMessageRow` inside to avoid colliding with the dataclass `InboundMessage` in `messaging/base.py`. The dataclass is the adapter contract value; the ORM row is persistence. They are intentionally different concerns.
- `api/routes/webhooks.py` and `api/routes/leads.py` are siblings of the existing `health.py`. Same pattern.
- Migration numbering: Plano 3 shipped `0005_kb_tables.py`. Plano 5 starts at `0006`.

---

## Prerequisites (delta from Plan 3)

Plan 3's prereqs (Docker, uv, age, sops, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) still apply. **No new system prereqs.** Plan 5 deps:

- `arq>=0.26` and `tenacity>=8` — Python libs added in Task 1.
- Redis is already running (Plan 1 brought it up via docker-compose) — arq uses the same instance.
- `ANTHROPIC_API_KEY` continues to drive Sonnet for the agent + Haiku for the critic.
- **No real WhatsApp number required** to develop/test. Live test (`tests/integration/test_messaging_e2e.py` with `LIVE_WHATSAPP=1`) is opt-in and gated by env var, mirroring the `live_llm` pattern from Plan 3 Task 19.

### Shared test fixtures (one-time setup)

The integration tests in this plan reference two shared pytest fixtures — `db_session` (an `AsyncSession` wired against the dev Postgres + tenant context helper friendly) and `app` (the FastAPI app instance). The repo does not yet have a `tests/conftest.py` defining these. Before running any integration test from Task 4 onward, create `tests/conftest.py`:

```python
"""Root pytest fixtures — shared by every test that needs DB + FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_sdr.main import create_app
from ai_sdr.settings import get_settings


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session against the running dev/test Postgres.

    Each test gets a fresh session that rolls back at the end so tests are
    isolated. Use `await db_session.commit()` inside the test only when you
    need cross-session visibility (e.g., the FastAPI app sees committed data)."""
    engine = create_async_engine(get_settings().database_url, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    """FastAPI app with lifespan executed (so arq_pool + adapter_registry
    are populated on app.state)."""
    a = create_app()
    async with a.router.lifespan_context(a):
        yield a
```

Commit this conftest in the same commit as Task 4 (the first integration test). If the integration tests in your Plano 2/3 baseline already use inline session creation, this conftest is purely additive — they keep working.

### VPS notes

Same VPS, same ports. After deploying, the operator must:

1. Add `wa_phone_id`, `wa_token`, `wa_verify`, `wa_app_secret` to `tenants/<slug>/secrets.enc.yaml` (SOPS-encrypted with the VPS age key).
2. Register the webhook URL `https://<host>/webhooks/<tenant_slug>/whatsapp_cloud` in the Meta Business Manager (uses `wa_verify` as the verify token).
3. Run the worker as a long-lived service: `docker compose up -d worker` (the new service from Task 24).

---

## Task 1: Add `arq` + `tenacity` dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependencies**

Open `pyproject.toml` and locate the `dependencies` array. Insert `"arq>=0.26",` and `"tenacity>=8",` alphabetically (arq before `asyncpg`, tenacity between `structlog` and `tiktoken`). The final array should contain (at minimum) these new lines in this order:

```toml
dependencies = [
    "alembic>=1.14",
    "anthropic>=0.40",
    "arq>=0.26",
    "asyncpg>=0.30",
    # ... existing deps unchanged ...
    "structlog>=24.4",
    "tenacity>=8",
    "tiktoken>=0.8",
    # ... rest unchanged ...
]
```

If you already have `arq` or `tenacity` listed, do not duplicate — verify and move on.

- [ ] **Step 2: Lock + install**

Run: `uv lock && uv sync`

Expected: lock file updated, both packages installed. No errors. If `uv sync` warns about Python version, that's pre-existing and unrelated.

- [ ] **Step 3: Smoke-import**

Run:
```bash
uv run python -c "import arq; import tenacity; print(arq.__version__, tenacity.__version__)"
```

Expected: prints version numbers like `0.26.x 8.x.x`. No `ImportError`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(plan5 t1): add arq + tenacity dependencies"
```

---

## Task 2: `MessagingConfig` schema + tenant.yaml integration

**Files:**
- Modify: `src/ai_sdr/schemas/tenant_yaml.py`
- Modify: `tests/unit/test_tenant_yaml.py`

**Design:** Add `MessagingConfig` Pydantic model and attach it as `messaging: MessagingConfig | None` on `TenantConfig`. Provider is a free-form `str` (factory dispatches). For `provider="whatsapp_cloud"` specifically, the validator requires `phone_number_id_ref`, `access_token_ref`, `webhook_verify_token_ref`, `app_secret_ref` — all of which must use the `secrets/` prefix convention.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tenant_yaml.py`:

```python
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def test_messaging_block_optional() -> None:
    cfg = TenantConfig.model_validate(_minimal_tenant_data())
    assert cfg.messaging is None


def test_messaging_whatsapp_cloud_full_block() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "secrets/wa_phone_id",
        "access_token_ref": "secrets/wa_token",
        "webhook_verify_token_ref": "secrets/wa_verify",
        "app_secret_ref": "secrets/wa_app_secret",
    }
    cfg = TenantConfig.model_validate(data)
    assert cfg.messaging is not None
    assert cfg.messaging.provider == "whatsapp_cloud"
    assert cfg.messaging.phone_number_id_ref == "secrets/wa_phone_id"
    assert cfg.messaging.api_version == "v21.0"  # default


def test_messaging_whatsapp_cloud_requires_all_refs() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "secrets/wa_phone_id",
        # missing access_token_ref, webhook_verify_token_ref, app_secret_ref
    }
    with pytest.raises(ValidationError, match="access_token_ref"):
        TenantConfig.model_validate(data)


def test_messaging_secrets_prefix_enforced() -> None:
    data = _minimal_tenant_data()
    data["messaging"] = {
        "provider": "whatsapp_cloud",
        "phone_number_id_ref": "wa_phone_id",  # missing secrets/ prefix
        "access_token_ref": "secrets/wa_token",
        "webhook_verify_token_ref": "secrets/wa_verify",
        "app_secret_ref": "secrets/wa_app_secret",
    }
    with pytest.raises(ValidationError, match=r"must start with 'secrets/'"):
        TenantConfig.model_validate(data)


def test_messaging_unknown_provider_allowed_at_schema_level() -> None:
    # provider is free-form str; factory dispatches. Schema only enforces
    # whatsapp_cloud-specific fields when provider == 'whatsapp_cloud'.
    data = _minimal_tenant_data()
    data["messaging"] = {"provider": "vialum_chat"}  # hypothetical future
    cfg = TenantConfig.model_validate(data)
    assert cfg.messaging is not None
    assert cfg.messaging.provider == "vialum_chat"
    assert cfg.messaging.phone_number_id_ref is None
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_tenant_yaml.py -v -k messaging`

Expected: FAIL with `ImportError: cannot import name 'MessagingConfig' from 'ai_sdr.schemas.tenant_yaml'`.

- [ ] **Step 3: Add `MessagingConfig`**

Open `src/ai_sdr/schemas/tenant_yaml.py`. Add this class above the `TenantConfig` class definition (preserve existing imports; add `model_validator` to the `pydantic` import if not present):

```python
class MessagingConfig(BaseModel):
    """Messaging provider config. provider is free-form; factory dispatches.

    For provider='whatsapp_cloud', the four *_ref fields are required and
    must use the 'secrets/' prefix (resolved by SopsLoader at runtime).
    """

    provider: str
    phone_number_id_ref: str | None = None
    access_token_ref: str | None = None
    webhook_verify_token_ref: str | None = None
    app_secret_ref: str | None = None
    api_version: str = "v21.0"

    @model_validator(mode="after")
    def _check_provider_fields(self) -> "MessagingConfig":
        if self.provider == "whatsapp_cloud":
            required = (
                "phone_number_id_ref",
                "access_token_ref",
                "webhook_verify_token_ref",
                "app_secret_ref",
            )
            for f in required:
                v = getattr(self, f)
                if not v:
                    raise ValueError(
                        f"messaging.whatsapp_cloud requires {f}"
                    )
                if not v.startswith("secrets/"):
                    raise ValueError(
                        f"messaging.{f} must start with 'secrets/' (got {v!r})"
                    )
        return self
```

- [ ] **Step 4: Attach to `TenantConfig`**

In the same file, add a `messaging` field to `TenantConfig`. Find the `TenantConfig` class and add (alphabetically by field, ideally between `llm` and `guardrails`):

```python
    messaging: MessagingConfig | None = None
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_tenant_yaml.py -v -k messaging`

Expected: all five `messaging` tests PASS. Other tests in the file should also still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/schemas/tenant_yaml.py tests/unit/test_tenant_yaml.py
git commit -m "feat(plan5 t2): MessagingConfig schema with whatsapp_cloud validation"
```

---

## Task 3: Migration 0006 — `leads` table

**Files:**
- Create: `migrations/versions/0006_leads_table.py`

**Design:** New table with `tenant_id` (RLS), `whatsapp_e164` (unique-per-tenant when set), `external_label` (unique-per-tenant when set, used by simulate's `--lead` flag), `status` enum-like text (`pending_assignment | active | unreachable`), `unreachable_reason` TEXT, timestamps. RLS policy `USING (tenant_id = current_setting('app.tenant_id', true)::uuid)` with FORCE so even the app role honors it. Follows the pattern in `0005_kb_tables.py`.

- [ ] **Step 1: Create the migration file**

Create `migrations/versions/0006_leads_table.py` with this content:

```python
"""leads table (with RLS + per-tenant unique indexes)

Revision ID: 0006_leads_table
Revises: 0005_kb_tables
Create Date: 2026-05-25 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0006_leads_table"
down_revision = "0005_kb_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_e164", sa.Text(), nullable=True),
        sa.Column("external_label", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending_assignment'"),
        ),
        sa.Column("unreachable_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending_assignment','active','unreachable')",
            name="ck_leads_status",
        ),
    )
    op.create_index(
        "uq_leads_tenant_wa",
        "leads",
        ["tenant_id", "whatsapp_e164"],
        unique=True,
        postgresql_where=sa.text("whatsapp_e164 IS NOT NULL"),
    )
    op.create_index(
        "uq_leads_tenant_label",
        "leads",
        ["tenant_id", "external_label"],
        unique=True,
        postgresql_where=sa.text("external_label IS NOT NULL"),
    )
    op.create_index("ix_leads_tenant_status", "leads", ["tenant_id", "status"])

    # RLS — same pattern as kb_documents
    op.execute("ALTER TABLE leads ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leads FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY leads_tenant_isolation ON leads
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS leads_tenant_isolation ON leads")
    op.drop_index("ix_leads_tenant_status", table_name="leads")
    op.drop_index("uq_leads_tenant_label", table_name="leads")
    op.drop_index("uq_leads_tenant_wa", table_name="leads")
    op.drop_table("leads")
```

- [ ] **Step 2: Run the migration locally**

Run: `make up` (ensures docker is running), then `uv run alembic upgrade head`.

Expected: migration applies cleanly. No errors. `psql` would show the `leads` table with three indexes and RLS enabled.

- [ ] **Step 3: Spot-check the table**

Run:
```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "\d+ leads"
```

Expected: shows columns, indexes, FK to `tenants`, check constraint. The `(force row security: yes)` line appears in the output.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0006_leads_table.py
git commit -m "feat(plan5 t3): leads table with RLS + per-tenant unique indexes"
```

---

## Task 4: `Lead` ORM model + RLS integration test

**Files:**
- Create: `src/ai_sdr/models/lead.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `tests/integration/test_leads_rls.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_leads_rls.py`:

```python
"""RLS test for the leads table — same pattern as kb_documents."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session, slug: str) -> Tenant:
    t = Tenant(slug=slug, display_name=slug.title())
    session.add(t)
    await session.flush()
    return t


async def test_lead_insert_and_select_under_tenant_context(db_session) -> None:
    tenant_a = await _make_tenant(db_session, f"a_{uuid.uuid4().hex[:6]}")
    tenant_b = await _make_tenant(db_session, f"b_{uuid.uuid4().hex[:6]}")
    await db_session.commit()

    # Insert lead under tenant A
    await set_tenant_context(db_session, tenant_a.id)
    db_session.add(Lead(tenant_id=tenant_a.id, whatsapp_e164="+5511999999991"))
    await db_session.commit()

    # Tenant A sees its lead
    await set_tenant_context(db_session, tenant_a.id)
    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert len(rows) == 1

    # Tenant B sees nothing
    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert rows == []


async def test_lead_external_label_unique_per_tenant(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    db_session.add(Lead(tenant_id=tenant.id, external_label="test-1"))
    await db_session.commit()

    # Same label, same tenant → conflict
    db_session.add(Lead(tenant_id=tenant.id, external_label="test-1"))
    with pytest.raises(Exception):  # IntegrityError or wrapped
        await db_session.commit()
    await db_session.rollback()


async def test_lead_status_check_constraint(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    db_session.add(Lead(tenant_id=tenant.id, status="nonsense_status"))
    with pytest.raises(Exception):
        await db_session.commit()
    await db_session.rollback()
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_leads_rls.py -v`

Expected: FAIL with `ImportError: cannot import name 'Lead' from 'ai_sdr.models.lead'`.

- [ ] **Step 3: Create the `Lead` model**

Create `src/ai_sdr/models/lead.py`:

```python
"""Lead — a person the agent talks to (or hasn't yet).

A lead is per-tenant (RLS-enforced). It carries an optional `whatsapp_e164`
(unique-per-tenant when set), an optional `external_label` (used by the
simulate CLI's --lead flag and any other dev/admin tooling that wants a
human-readable handle), and a status that gates the worker's behavior:

  - 'pending_assignment' — new lead from inbound; messages queue but no step()
    runs until an operator assigns a treeflow via CLI/REST.
  - 'active' — has an attached talkflow; worker drains inbox via runtime.step().
  - 'unreachable' — provider returned RecipientUnreachable; new inbounds get
    skipped (status_skipped) rather than driving step().
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base

LeadStatus = Literal["pending_assignment", "active", "unreachable"]


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
    status: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="pending_assignment"
    )
    unreachable_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
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

- [ ] **Step 4: Re-export from models package**

Open `src/ai_sdr/models/__init__.py` and add the import + entry to `__all__`:

```python
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

__all__ = ["KbChunk", "KbDocument", "Lead", "TalkFlow", "Tenant", "TreeflowVersion"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/integration/test_leads_rls.py -v`

Expected: all three tests PASS. Requires `make up` to be running (Postgres + Redis containers).

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/lead.py src/ai_sdr/models/__init__.py tests/integration/test_leads_rls.py
git commit -m "feat(plan5 t4): Lead ORM model + RLS integration tests"
```

---

## Task 5: Migration 0007 — `inbound_messages` table

**Files:**
- Create: `migrations/versions/0007_inbound_messages_table.py`

**Design:** UNIQUE on `(tenant_id, provider, external_id)` is the dedupe key — WhatsApp retrying the same webhook produces an `ON CONFLICT DO NOTHING` no-op insert. `lead_id` is `ON DELETE SET NULL` so deleting a lead during cleanup doesn't blow up audit history. `raw` is `JSONB` (full payload, audit). Status check constraint mirrors the spec's allowed values.

- [ ] **Step 1: Create the migration file**

Create `migrations/versions/0007_inbound_messages_table.py`:

```python
"""inbound_messages table (with RLS + dedupe unique + status check)

Revision ID: 0007_inbound_messages_table
Revises: 0006_leads_table
Create Date: 2026-05-25 00:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007_inbound_messages_table"
down_revision = "0006_leads_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_messages",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            nullable=False,
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), nullable=True),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("raw", JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id", "provider", "external_id",
            name="uq_inbound_provider_extid",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processed','skipped_dedupe','error')",
            name="ck_inbound_messages_status",
        ),
    )
    op.create_index(
        "ix_inbound_lead_status",
        "inbound_messages",
        ["lead_id", "status"],
        postgresql_where=sa.text("status IN ('queued','error')"),
    )

    op.execute("ALTER TABLE inbound_messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE inbound_messages FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY inbound_messages_tenant_isolation ON inbound_messages
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS inbound_messages_tenant_isolation ON inbound_messages")
    op.drop_index("ix_inbound_lead_status", table_name="inbound_messages")
    op.drop_table("inbound_messages")
```

- [ ] **Step 2: Apply migration**

Run: `uv run alembic upgrade head`

Expected: applies cleanly, no errors.

- [ ] **Step 3: Spot-check**

Run:
```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "\d+ inbound_messages"
```

Expected: shows columns, the partial index, check constraint, RLS enabled.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0007_inbound_messages_table.py
git commit -m "feat(plan5 t5): inbound_messages table with dedupe unique + RLS"
```

---

## Task 6: `InboundMessageRow` ORM model + RLS integration test

**Files:**
- Create: `src/ai_sdr/models/inbound_message.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `tests/integration/test_inbound_messages_rls.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_inbound_messages_rls.py`:

```python
"""RLS + dedupe test for inbound_messages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session, slug: str) -> Tenant:
    t = Tenant(slug=slug, display_name=slug.title())
    session.add(t)
    await session.flush()
    return t


async def test_inbound_rls_isolation(db_session) -> None:
    tenant_a = await _make_tenant(db_session, f"a_{uuid.uuid4().hex[:6]}")
    tenant_b = await _make_tenant(db_session, f"b_{uuid.uuid4().hex[:6]}")
    await db_session.commit()

    await set_tenant_context(db_session, tenant_a.id)
    lead_a = Lead(tenant_id=tenant_a.id, whatsapp_e164="+5511999999991", status="active")
    db_session.add(lead_a)
    await db_session.flush()
    db_session.add(InboundMessageRow(
        tenant_id=tenant_a.id, provider="whatsapp_cloud", external_id="wa_msg_1",
        lead_id=lead_a.id, from_address="+5511999999991", text="oi",
        received_at=datetime.now(timezone.utc), raw={"id": "wa_msg_1"},
    ))
    await db_session.commit()

    await set_tenant_context(db_session, tenant_b.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert rows == []


async def test_inbound_dedupe_via_on_conflict(db_session) -> None:
    tenant = await _make_tenant(db_session, f"t_{uuid.uuid4().hex[:6]}")
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status="active")
    db_session.add(lead)
    await db_session.flush()

    values = {
        "tenant_id": tenant.id, "provider": "whatsapp_cloud", "external_id": "dup_1",
        "lead_id": lead.id, "from_address": "+5511999999999", "text": "first",
        "received_at": datetime.now(timezone.utc), "raw": {"id": "dup_1"},
    }
    r1 = await db_session.execute(
        pg_insert(InboundMessageRow).values(**values).on_conflict_do_nothing()
    )
    r2 = await db_session.execute(
        pg_insert(InboundMessageRow).values(**{**values, "text": "second"})
        .on_conflict_do_nothing()
    )
    await db_session.commit()
    assert r1.rowcount == 1
    assert r2.rowcount == 0

    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "first"  # second was rejected silently
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_inbound_messages_rls.py -v`

Expected: FAIL with `ImportError: cannot import name 'InboundMessageRow' from 'ai_sdr.models.inbound_message'`.

- [ ] **Step 3: Create the ORM model**

Create `src/ai_sdr/models/inbound_message.py`:

```python
"""InboundMessageRow — persistence for inbound provider messages.

Naming: the dataclass `InboundMessage` in `ai_sdr.messaging.base` is the
adapter contract value type (an in-memory normalized message). This is
the ORM row that persists it for dedupe, audit, and replay. They are
intentionally different concerns — keep both.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class InboundMessageRow(Base):
    __tablename__ = "inbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text(), nullable=False)
    external_id: Mapped[str] = mapped_column(Text(), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_address: Mapped[str] = mapped_column(Text(), nullable=False)
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text(), nullable=False, server_default="queued"
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
```

- [ ] **Step 4: Re-export from models package**

Open `src/ai_sdr/models/__init__.py` and update:

```python
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.kb_chunk import KbChunk
from ai_sdr.models.kb_document import KbDocument
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

__all__ = [
    "InboundMessageRow",
    "KbChunk",
    "KbDocument",
    "Lead",
    "TalkFlow",
    "Tenant",
    "TreeflowVersion",
]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/integration/test_inbound_messages_rls.py -v`

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/inbound_message.py src/ai_sdr/models/__init__.py tests/integration/test_inbound_messages_rls.py
git commit -m "feat(plan5 t6): InboundMessageRow ORM + dedupe integration tests"
```

---

## Task 7: Migration 0008 — `talkflows.lead_id` String → UUID FK

**Files:**
- Create: `migrations/versions/0008_talkflows_lead_id_fk.py`

**Design:** Today `talkflows.lead_id` is `String(128)` (e.g., `"test-1"` from the simulate CLI). It becomes `UUID NOT NULL REFERENCES leads(id)`. Backfill plan: for each distinct `(tenant_id, lead_id_old)` in talkflows, insert a row into `leads` with `external_label=lead_id_old`, `status='active'`, `whatsapp_e164=NULL`. Then add `lead_uuid` column, populate it from the join, drop the old column, rename `lead_uuid → lead_id`, re-add the FK + UNIQUE constraint. In dev (DBs empty of talkflows) the backfill INSERT loops over zero rows — no-op.

- [ ] **Step 1: Create the migration**

Create `migrations/versions/0008_talkflows_lead_id_fk.py`:

```python
"""talkflows.lead_id String → UUID FK to leads(id)

Revision ID: 0008_talkflows_lead_id_fk
Revises: 0007_inbound_messages_table
Create Date: 2026-05-25 00:00:00

Backfill: for each distinct (tenant_id, lead_id) in existing talkflows,
create a Lead row with external_label=<old_string>, status='active',
whatsapp_e164=NULL. Then point talkflows at the new UUIDs.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_talkflows_lead_id_fk"
down_revision = "0007_inbound_messages_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Backfill: insert one Lead row per distinct (tenant_id, lead_id) in talkflows.
    #    ON CONFLICT DO NOTHING in case multiple talkflows share a label
    #    (the unique constraint uq_leads_tenant_label catches it).
    op.execute(
        """
        INSERT INTO leads (tenant_id, external_label, status)
        SELECT DISTINCT tenant_id, lead_id, 'active'
        FROM talkflows
        ON CONFLICT (tenant_id, external_label)
            WHERE external_label IS NOT NULL
            DO NOTHING
        """
    )

    # 2. Drop existing unique constraint on (tenant_id, lead_id) — we'll recreate
    #    it on the new UUID column.
    op.drop_constraint("uq_talkflows_tenant_lead", "talkflows", type_="unique")

    # 3. Add the new UUID column (nullable for the duration of the data move).
    op.add_column(
        "talkflows",
        sa.Column("lead_uuid", UUID(as_uuid=True), nullable=True),
    )

    # 4. Populate lead_uuid from leads.id via the external_label join.
    op.execute(
        """
        UPDATE talkflows tf
        SET lead_uuid = l.id
        FROM leads l
        WHERE l.tenant_id = tf.tenant_id
          AND l.external_label = tf.lead_id
        """
    )

    # 5. Drop the old string column.
    op.drop_column("talkflows", "lead_id")

    # 6. Rename lead_uuid → lead_id.
    op.alter_column("talkflows", "lead_uuid", new_column_name="lead_id")

    # 7. Add NOT NULL + FK + unique constraint.
    op.alter_column(
        "talkflows", "lead_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_talkflows_lead_id",
        "talkflows",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_talkflows_tenant_lead",
        "talkflows",
        ["tenant_id", "lead_id"],
    )


def downgrade() -> None:
    # Best-effort: replace UUID with the lead's external_label. Lossy if a
    # lead has no external_label (e.g., one created by the webhook handler).
    op.drop_constraint("uq_talkflows_tenant_lead", "talkflows", type_="unique")
    op.drop_constraint("fk_talkflows_lead_id", "talkflows", type_="foreignkey")
    op.add_column(
        "talkflows", sa.Column("lead_id_str", sa.String(length=128), nullable=True)
    )
    op.execute(
        """
        UPDATE talkflows tf
        SET lead_id_str = COALESCE(l.external_label, l.id::text)
        FROM leads l
        WHERE l.id = tf.lead_id
        """
    )
    op.drop_column("talkflows", "lead_id")
    op.alter_column("talkflows", "lead_id_str", new_column_name="lead_id")
    op.alter_column(
        "talkflows", "lead_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_talkflows_tenant_lead", "talkflows", ["tenant_id", "lead_id"]
    )
```

- [ ] **Step 2: Apply migration**

Run: `uv run alembic upgrade head`

Expected: applies cleanly. If there are pre-existing talkflows in your dev DB from Plano 2/3 simulate runs, a few rows in `leads` will be created from them.

- [ ] **Step 3: Verify the schema change**

Run:
```bash
docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "\d talkflows"
```

Expected: `lead_id | uuid | not null` (was previously `character varying(128)`), and an `fk_talkflows_lead_id` FK constraint to `leads(id)`.

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/0008_talkflows_lead_id_fk.py
git commit -m "feat(plan5 t7): talkflows.lead_id String → UUID FK with backfill"
```

---

## Task 8: Update `TalkFlow` ORM model

**Files:**
- Modify: `src/ai_sdr/models/talkflow.py`
- Create: `tests/integration/test_talkflows_lead_fk.py`

**Design:** Change `lead_id: Mapped[str]` to `Mapped[uuid.UUID]` with a real FK to `leads.id`. The Python type change cascades to `TalkFlowRuntime.create()` (Task 9 below uses the new type).

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_talkflows_lead_fk.py`:

```python
"""Verify talkflows.lead_id is a UUID FK to leads.id after migration 0008."""

from __future__ import annotations

import uuid

import pytest

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.models.talkflow import TalkFlow

pytestmark = pytest.mark.integration


async def test_talkflow_lead_id_is_uuid_fk(db_session) -> None:
    tenant = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    db_session.add(tenant)
    await db_session.flush()

    # Create a treeflow version (TalkFlow needs it as FK target)
    tv = TreeflowVersion(
        tenant_id=tenant.id,
        treeflow_id="t1",
        version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes:\n  n1: {prompt: hi}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, external_label="x", status="active")
    db_session.add(lead)
    await db_session.flush()

    tf = TalkFlow(
        tenant_id=tenant.id,
        lead_id=lead.id,  # MUST accept uuid.UUID now
        treeflow_version_id=tv.id,
        thread_id=f"{tenant.id}:{uuid.uuid4()}",
    )
    db_session.add(tf)
    await db_session.commit()
    assert isinstance(tf.lead_id, uuid.UUID)
```

- [ ] **Step 2: Run (expect fail at runtime or type-check)**

Run: `uv run pytest tests/integration/test_talkflows_lead_fk.py -v`

Expected: FAIL — `TalkFlow.lead_id` still typed as `Mapped[str]`, passing a UUID either raises or coerces wrongly.

- [ ] **Step 3: Update the model**

Open `src/ai_sdr/models/talkflow.py`. Replace the existing `lead_id` line with a UUID FK. Add the `ForeignKey` import if not already present:

```python
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
    __table_args__ = (UniqueConstraint("tenant_id", "lead_id", name="uq_talkflows_tenant_lead"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.uuid_generate_v4()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="RESTRICT"),
        nullable=False,
    )
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
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_talkflows_lead_fk.py -v`

Expected: PASS.

- [ ] **Step 5: Run the full integration suite to catch any regressions in existing talkflow tests**

Run: `uv run pytest tests/integration/ -v`

Expected: all PASS. If anything fails because it was passing a string `lead_id` to TalkFlow rows, fix the test to create a Lead first and pass its UUID. (Plano 2/3 tests should be self-contained.)

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/talkflow.py tests/integration/test_talkflows_lead_fk.py
git commit -m "feat(plan5 t8): TalkFlow.lead_id Mapped[uuid.UUID] FK to leads"
```

---

## Task 9: `messaging/errors.py` + tests

**Files:**
- Create: `src/ai_sdr/messaging/__init__.py`
- Create: `src/ai_sdr/messaging/errors.py`
- Create: `tests/unit/test_messaging_errors.py`

**Design:** A hierarchy of typed exceptions. `MessagingError` is the base; `SignatureError` and `TerminalError` are direct children. Under `TerminalError`: `AuthError`, `RecipientUnreachable`, `WindowExpiredError`, `PolicyError`. Internal-only: `TransientError` and `RateLimitError(retry_after_s)` — these never escape the adapter (adapter swallows them via tenacity retry; if exhausted, re-raises as the original terminal error or wraps in `MessagingError`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_messaging_errors.py`:

```python
"""Exception hierarchy: every typed error inherits MessagingError;
terminal errors inherit TerminalError; RateLimitError carries retry_after_s."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    SignatureError,
    TerminalError,
    TransientError,
    WindowExpiredError,
)


@pytest.mark.parametrize(
    "exc_type",
    [SignatureError, TerminalError, TransientError, AuthError, PolicyError,
     RecipientUnreachable, WindowExpiredError, RateLimitError],
)
def test_all_inherit_messaging_error(exc_type) -> None:
    assert issubclass(exc_type, MessagingError)


@pytest.mark.parametrize(
    "exc_type",
    [AuthError, PolicyError, RecipientUnreachable, WindowExpiredError],
)
def test_terminal_subclasses(exc_type) -> None:
    assert issubclass(exc_type, TerminalError)


def test_rate_limit_inherits_transient() -> None:
    assert issubclass(RateLimitError, TransientError)


def test_rate_limit_carries_retry_after() -> None:
    e = RateLimitError(retry_after_s=42)
    assert e.retry_after_s == 42
    assert "42" in str(e) or "retry_after" in str(e)
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_errors.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_sdr.messaging'`.

- [ ] **Step 3: Create the package + errors module**

Create `src/ai_sdr/messaging/__init__.py` as an empty file (just a newline).

Create `src/ai_sdr/messaging/errors.py`:

```python
"""Exception hierarchy for the messaging adapter boundary.

Terminal vs transient is the key distinction:
  - SignatureError       webhook auth failed → caller returns 401.
  - TerminalError        adapter gave up. Worker decides next action per subtype:
      - AuthError           bad provider token → log + alert ops.
      - RecipientUnreachable number not on the channel → mark lead.unreachable.
      - WindowExpiredError  outside 24h window → Plano 9 hook (template HSM).
      - PolicyError         provider policy violation → log + alert ops.
  - TransientError       adapter SHOULD retry internally; never re-raised.
      - RateLimitError      provider 429 with Retry-After header.
"""

from __future__ import annotations


class MessagingError(Exception):
    """Base for any messaging-related error."""


class SignatureError(MessagingError):
    """Webhook signature (HMAC) verification failed. Caller returns HTTP 401."""


class TerminalError(MessagingError):
    """Adapter exhausted internal retries. Worker handles per subtype."""


class AuthError(TerminalError):
    """Provider rejected the credentials (401/403/code 190 in WhatsApp)."""


class RecipientUnreachable(TerminalError):
    """The destination address cannot receive messages on this channel."""


class WindowExpiredError(TerminalError):
    """The 24h messaging window expired; only templates are allowed."""


class PolicyError(TerminalError):
    """Provider rejected the message content (spam/policy violation)."""


class TransientError(MessagingError):
    """Recoverable error; adapter retries internally with backoff."""


class RateLimitError(TransientError):
    """Provider rate-limited; adapter must respect retry_after_s."""

    def __init__(self, retry_after_s: int):
        super().__init__(f"rate limited; retry_after_s={retry_after_s}")
        self.retry_after_s = retry_after_s
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_messaging_errors.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/__init__.py src/ai_sdr/messaging/errors.py tests/unit/test_messaging_errors.py
git commit -m "feat(plan5 t9): MessagingError exception hierarchy"
```

---

## Task 10: `messaging/base.py` — dataclasses + ABC

**Files:**
- Create: `src/ai_sdr/messaging/base.py`
- Create: `tests/unit/test_messaging_base.py`

**Design:** `InboundMessage` and `SendResult` are frozen dataclasses (immutable; adapter values flow one-way to the runtime). `MessagingAdapter` is a Python ABC with three abstract methods (`handle_inbound`, `send_text`, `verification_challenge`). Concrete subclasses must implement all three; we assert this in the test.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_messaging_base.py`:

```python
"""Contract surface tests for messaging.base."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Mapping

import pytest

from ai_sdr.messaging.base import (
    InboundMessage,
    MessagingAdapter,
    SendResult,
)


def test_inbound_message_is_frozen() -> None:
    m = InboundMessage(
        external_id="wa_1",
        from_address="+5511999999999",
        text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00",
        raw={"id": "wa_1"},
    )
    with pytest.raises(FrozenInstanceError):
        m.text = "tampered"  # type: ignore[misc]


def test_send_result_is_frozen() -> None:
    r = SendResult(external_id="wa_sent_1", sent_at_iso="2026-05-25T12:00:01+00:00")
    with pytest.raises(FrozenInstanceError):
        r.external_id = "x"  # type: ignore[misc]


def test_cannot_instantiate_abstract_adapter() -> None:
    with pytest.raises(TypeError, match="abstract"):
        MessagingAdapter()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated() -> None:
    class Dummy(MessagingAdapter):
        async def handle_inbound(self, raw_body: bytes, headers: Mapping[str, str]):
            return []

        async def send_text(self, to: str, text: str) -> SendResult:
            return SendResult(external_id="x", sent_at_iso="now")

        def verification_challenge(self, params: Mapping[str, str]) -> str | None:
            return None

    d = Dummy()
    assert isinstance(d, MessagingAdapter)
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_base.py -v`

Expected: FAIL with `ImportError: cannot import name 'InboundMessage'`.

- [ ] **Step 3: Create the module**

Create `src/ai_sdr/messaging/base.py`:

```python
"""MessagingAdapter contract — the boundary between PeSDR runtime and a
messaging provider.

Adapter purity invariants:
  - Knows nothing about the `leads` or `tenants` tables.
  - Speaks opaque provider-native addresses via `to: str` (E.164 for
    WhatsApp Cloud, `vialum_contact_id` for a future Vialum adapter, etc).
  - Receives tenant-specific config + secrets at construction; never reads
    them at request time.
  - Retries Transient/RateLimit errors internally with bounded backoff;
    surfaces only TerminalError subtypes (plus SignatureError on inbound).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class InboundMessage:
    """A normalized inbound message produced by handle_inbound().

    `external_id` is the provider-native message id used for idempotent
    dedupe at the persistence layer. `from_address` is the provider-native
    sender address (E.164 for WhatsApp); the runtime resolves it to a
    lead via find_or_create_lead_by_address(). `raw` is the full original
    payload, persisted for audit.
    """

    external_id: str
    from_address: str
    text: str
    received_at_iso: str
    raw: Mapping[str, object]


@dataclass(frozen=True)
class SendResult:
    """Successful delivery — what the worker logs and persists."""

    external_id: str
    sent_at_iso: str


class MessagingAdapter(ABC):
    """Boundary between PeSDR runtime and a messaging provider."""

    @abstractmethod
    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        """Verify signature, parse, normalize.

        Returns []:
          - for challenge/verification requests that arrive at the POST URL
            (some providers do this; WhatsApp uses GET so it's a no-op here);
          - for status updates / read receipts / typing indicators;
          - for non-text messages (Plano 5 ignores audio/image/document —
            Plano 8 will re-introduce them as MediaPart).

        Raises SignatureError if HMAC verification fails. Caller returns 401.
        """

    @abstractmethod
    async def send_text(self, to: str, text: str) -> SendResult:
        """Deliver text to recipient. Adapter retries Transient/RateLimit
        internally with bounded backoff. Raises typed terminal errors:
        AuthError, RecipientUnreachable, WindowExpiredError, PolicyError.
        """

    @abstractmethod
    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        """For providers with a GET-based webhook challenge (WhatsApp's
        hub.mode=subscribe handshake). Returns the challenge token to echo.
        Returns None if this provider has no challenge step (and the caller
        should return 404)."""
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_messaging_base.py -v`

Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/base.py tests/unit/test_messaging_base.py
git commit -m "feat(plan5 t10): MessagingAdapter ABC + InboundMessage/SendResult dataclasses"
```

---

## Task 11: `messaging/fake.py` — `FakeMessagingAdapter`

**Files:**
- Create: `src/ai_sdr/messaging/fake.py`
- Create: `tests/unit/test_messaging_fake.py`

**Design:** In-memory adapter for unit tests of the worker, the ingest helpers, and (eventually) the simulate CLI. Supports scripting:
- `queue_inbound(InboundMessage)` → next `handle_inbound()` call returns the queued list and clears it.
- `fail_next_send(exc: TerminalError)` → next `send_text()` raises the given exception once.
- `sent_messages: list[tuple[to, text]]` records all successful sends.
- `verification_challenge` echoes whatever `hub.challenge` arrives without checking.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_messaging_fake.py`:

```python
"""FakeMessagingAdapter behavioral tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter


async def test_handle_inbound_returns_queued_then_empties() -> None:
    fake = FakeMessagingAdapter()
    msg = InboundMessage(
        external_id="m1", from_address="+5511999999999", text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00", raw={"id": "m1"},
    )
    fake.queue_inbound(msg)

    out = await fake.handle_inbound(b"", {})
    assert out == [msg]

    out_again = await fake.handle_inbound(b"", {})
    assert out_again == []  # queue drained


async def test_send_text_records_sent_messages() -> None:
    fake = FakeMessagingAdapter()
    r1 = await fake.send_text("+5511999999991", "hello")
    r2 = await fake.send_text("+5511999999992", "world")
    assert fake.sent_messages == [
        ("+5511999999991", "hello"),
        ("+5511999999992", "world"),
    ]
    assert r1.external_id != r2.external_id


async def test_fail_next_send_raises_once() -> None:
    fake = FakeMessagingAdapter()
    fake.fail_next_send(RecipientUnreachable("number not on WA"))

    with pytest.raises(RecipientUnreachable):
        await fake.send_text("+5511999999999", "x")

    # Subsequent send succeeds
    r = await fake.send_text("+5511999999999", "y")
    assert r.external_id


def test_verification_challenge_echoes() -> None:
    fake = FakeMessagingAdapter()
    assert fake.verification_challenge({"hub.challenge": "abc123"}) == "abc123"
    assert fake.verification_challenge({}) is None
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_fake.py -v`

Expected: FAIL with `ImportError: cannot import name 'FakeMessagingAdapter'`.

- [ ] **Step 3: Create the fake**

Create `src/ai_sdr/messaging/fake.py`:

```python
"""In-memory MessagingAdapter for tests and the simulate CLI.

Supports scripting: queue inbound messages, force failures on next send,
inspect what was sent. No I/O, no provider integration.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Mapping

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult
from ai_sdr.messaging.errors import TerminalError


class FakeMessagingAdapter(MessagingAdapter):
    """Test/dev adapter. Not for production use."""

    def __init__(self) -> None:
        self._inbound_queue: deque[InboundMessage] = deque()
        self._pending_failure: TerminalError | None = None
        self.sent_messages: list[tuple[str, str]] = []

    # --- scripting hooks --------------------------------------------------

    def queue_inbound(self, msg: InboundMessage) -> None:
        """Make the next handle_inbound() return this (along with any other
        previously queued messages). Each call to handle_inbound() drains
        the entire queue."""
        self._inbound_queue.append(msg)

    def fail_next_send(self, exc: TerminalError) -> None:
        """Make the next (single) send_text() raise this. Subsequent sends
        succeed normally."""
        self._pending_failure = exc

    # --- adapter interface ------------------------------------------------

    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        out = list(self._inbound_queue)
        self._inbound_queue.clear()
        return out

    async def send_text(self, to: str, text: str) -> SendResult:
        if self._pending_failure is not None:
            exc = self._pending_failure
            self._pending_failure = None
            raise exc
        self.sent_messages.append((to, text))
        return SendResult(
            external_id=f"fake_{uuid.uuid4().hex[:12]}",
            sent_at_iso=datetime.now(timezone.utc).isoformat(),
        )

    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        return params.get("hub.challenge")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_messaging_fake.py -v`

Expected: all four tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/fake.py tests/unit/test_messaging_fake.py
git commit -m "feat(plan5 t11): FakeMessagingAdapter for tests + simulator"
```

---

## Task 12: `messaging/factory.py` — provider dispatch

**Files:**
- Create: `src/ai_sdr/messaging/factory.py`
- Create: `tests/unit/test_messaging_factory.py`

**Design:** `build_messaging_adapter(cfg, secrets)` reads `cfg.provider`, looks up the impl class in a dict (`whatsapp_cloud → WhatsAppCloudAPIAdapter`, `fake → FakeMessagingAdapter`), and constructs it with the resolved secrets. The factory **strips** the `secrets/` prefix from `*_ref` config fields and looks them up in `secrets` by bare name (same convention as `llm/factory.py`). Unknown providers raise `ValueError`. The actual WhatsAppCloudAPIAdapter class is referenced but not implemented yet — Task 13+ — so we forward-declare it with `from __future__ import annotations` and use a string in the dict, OR we register impls via a registry pattern. We use a registry decorator to keep it simple.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_messaging_factory.py`:

```python
"""Factory dispatch tests."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.factory import build_messaging_adapter
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def test_factory_returns_fake_adapter() -> None:
    cfg = MessagingConfig(provider="fake")
    a = build_messaging_adapter(cfg, secrets={})
    assert isinstance(a, FakeMessagingAdapter)


def test_factory_unknown_provider_raises() -> None:
    cfg = MessagingConfig(provider="not_a_provider")
    with pytest.raises(ValueError, match="unknown messaging provider"):
        build_messaging_adapter(cfg, secrets={})


def test_factory_builds_whatsapp_cloud_with_secrets() -> None:
    """The whatsapp_cloud impl is registered in Task 13 — by then this passes.
    For now we just assert dispatch happens without TypeError on the
    well-formed config + secrets shape."""
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "111", "wa_token": "EAA...",
        "wa_verify": "vt", "wa_app_secret": "as",
    }
    a = build_messaging_adapter(cfg, secrets=secrets)
    # We assert duck-typed attributes the adapter is constructed with;
    # the concrete WhatsAppCloudAPIAdapter implementation lands in Task 13.
    assert a is not None
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_factory.py -v`

Expected: FAIL with `ImportError: cannot import name 'build_messaging_adapter'`.

- [ ] **Step 3: Create the factory**

Create `src/ai_sdr/messaging/factory.py`:

```python
"""Build a MessagingAdapter from MessagingConfig + resolved secrets.

Same dispatch pattern as `ai_sdr.llm.factory.build_llm` — providers are
registered in a dict keyed by the `provider` string from tenant.yaml.

The `secrets/` prefix on `*_ref` config fields is stripped before lookup,
matching the convention enforced by MessagingConfig's validator.
"""

from __future__ import annotations

from typing import Callable, Mapping

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

# Registry of (provider_name → builder callable).
# Builders take (cfg, secrets) and return a MessagingAdapter.
_REGISTRY: dict[str, Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]] = {}


def register_provider(
    name: str,
) -> Callable[
    [Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]],
    Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter],
]:
    """Decorator: registers a builder under `name`. Used by impl modules
    (whatsapp_cloud.py) so they don't have to import the factory."""

    def _wrap(
        builder: Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter],
    ) -> Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]:
        if name in _REGISTRY:
            raise RuntimeError(f"messaging provider already registered: {name}")
        _REGISTRY[name] = builder
        return builder

    return _wrap


def _resolve_secret(ref: str | None, secrets: Mapping[str, str]) -> str | None:
    if ref is None:
        return None
    if not ref.startswith("secrets/"):
        raise ValueError(f"secret ref must start with 'secrets/' (got {ref!r})")
    bare = ref[len("secrets/"):]
    if bare not in secrets:
        raise KeyError(f"secret {bare!r} not present in resolved secrets")
    return secrets[bare]


# --- built-in registrations ---------------------------------------------------


@register_provider("fake")
def _build_fake(
    cfg: MessagingConfig, secrets: Mapping[str, str]
) -> MessagingAdapter:
    return FakeMessagingAdapter()


def build_messaging_adapter(
    cfg: MessagingConfig, secrets: Mapping[str, str]
) -> MessagingAdapter:
    # Importing whatsapp_cloud triggers its @register_provider("whatsapp_cloud")
    # side-effect. Done lazily so unit tests of the factory don't require
    # httpx/tenacity stacks just to dispatch to FakeMessagingAdapter.
    if "whatsapp_cloud" not in _REGISTRY:
        from ai_sdr.messaging import whatsapp_cloud  # noqa: F401

    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown messaging provider: {cfg.provider!r}")
    return builder(cfg, secrets)
```

**Note:** The `whatsapp_cloud` module is imported lazily — that file is created in Task 13. Until then, the third test asserts only that dispatch reaches the builder. Once Task 13 lands and `WhatsAppCloudAPIAdapter` is registered, the third test will fully construct a real adapter instance.

- [ ] **Step 4: Stub the whatsapp_cloud module just enough to land**

For the import in `factory.py` to not crash before Task 13, create a placeholder `src/ai_sdr/messaging/whatsapp_cloud.py`:

```python
"""Placeholder — real implementation lands in Plano 5 Task 13."""

from __future__ import annotations

from typing import Mapping

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.factory import register_provider
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(
    cfg: MessagingConfig, secrets: Mapping[str, str]
) -> MessagingAdapter:
    """Replaced in Task 13 with the real WhatsAppCloudAPIAdapter."""
    raise NotImplementedError(
        "WhatsAppCloudAPIAdapter lands in Plano 5 Task 13. "
        "Factory dispatch is wired but the impl is pending."
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_messaging_factory.py -v`

Expected: first two tests PASS; the third PASSES only if we adjust to expect a NotImplementedError. Update the third test to assert the placeholder raises:

```python
def test_factory_builds_whatsapp_cloud_with_secrets() -> None:
    """Stub registered in Task 12 — full impl arrives in Task 13."""
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "111", "wa_token": "EAA...",
        "wa_verify": "vt", "wa_app_secret": "as",
    }
    with pytest.raises(NotImplementedError, match="Task 13"):
        build_messaging_adapter(cfg, secrets=secrets)
```

Re-run: `uv run pytest tests/unit/test_messaging_factory.py -v` → all three PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/factory.py src/ai_sdr/messaging/whatsapp_cloud.py tests/unit/test_messaging_factory.py
git commit -m "feat(plan5 t12): messaging factory dispatch + whatsapp_cloud placeholder"
```

---

## Task 13: `WhatsAppCloudAPIAdapter` scaffold + `verification_challenge`

**Files:**
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Create: `tests/unit/test_whatsapp_challenge.py`

**Design:** Replace the placeholder from Task 12 with the real class. This task only implements `verification_challenge` and the `__init__` that resolves secrets. `handle_inbound` and `send_text` are stubbed with `NotImplementedError` and filled in Tasks 14/15.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_whatsapp_challenge.py`:

```python
"""verification_challenge: WhatsApp's hub.mode=subscribe handshake."""

from __future__ import annotations

import pytest

from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def _adapter(verify_token: str = "vt_secret") -> WhatsAppCloudAPIAdapter:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999111", "wa_token": "EAA...",
        "wa_verify": verify_token, "wa_app_secret": "as",
    }
    return WhatsAppCloudAPIAdapter(cfg, secrets)


def test_challenge_echoes_when_token_matches() -> None:
    a = _adapter("vt_secret")
    out = a.verification_challenge({
        "hub.mode": "subscribe",
        "hub.verify_token": "vt_secret",
        "hub.challenge": "abc123",
    })
    assert out == "abc123"


def test_challenge_returns_none_when_mode_not_subscribe() -> None:
    a = _adapter("vt_secret")
    out = a.verification_challenge({
        "hub.mode": "something_else",
        "hub.verify_token": "vt_secret",
        "hub.challenge": "abc123",
    })
    assert out is None


def test_challenge_raises_when_token_mismatch() -> None:
    a = _adapter("vt_secret")
    with pytest.raises(SignatureError, match="verify token"):
        a.verification_challenge({
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG",
            "hub.challenge": "abc123",
        })
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_whatsapp_challenge.py -v`

Expected: FAIL — the placeholder raises NotImplementedError when constructing.

- [ ] **Step 3: Replace the placeholder with the real class**

Overwrite `src/ai_sdr/messaging/whatsapp_cloud.py` with this content:

```python
"""WhatsAppCloudAPIAdapter — default standalone messaging impl.

Implements the WhatsApp Cloud API surface PeSDR needs for Plano 5:
  - GET webhook verification (hub.mode=subscribe handshake)
  - POST webhook ingestion: HMAC verify + parse text messages
  - send_text via Graph API with bounded retry + typed error classification

Configuration comes from MessagingConfig; secrets are resolved by the
factory before construction (see `_build_whatsapp_cloud` below).
"""

from __future__ import annotations

from typing import Mapping

from ai_sdr.messaging.base import (
    InboundMessage,
    MessagingAdapter,
    SendResult,
)
from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.factory import register_provider
from ai_sdr.schemas.tenant_yaml import MessagingConfig


class WhatsAppCloudAPIAdapter(MessagingAdapter):
    """Production-grade adapter for WhatsApp Cloud API (Meta Graph)."""

    def __init__(self, cfg: MessagingConfig, secrets: Mapping[str, str]) -> None:
        if cfg.provider != "whatsapp_cloud":
            raise ValueError(
                f"WhatsAppCloudAPIAdapter requires provider='whatsapp_cloud' "
                f"(got {cfg.provider!r})"
            )
        # The factory has already validated *_ref shape; here we just bare-
        # name lookup the resolved secrets.
        self._phone_number_id = secrets[cfg.phone_number_id_ref.removeprefix("secrets/")]
        self._access_token = secrets[cfg.access_token_ref.removeprefix("secrets/")]
        self._verify_token = secrets[cfg.webhook_verify_token_ref.removeprefix("secrets/")]
        self._app_secret = secrets[cfg.app_secret_ref.removeprefix("secrets/")]
        self._api_version = cfg.api_version

    def verification_challenge(self, params: Mapping[str, str]) -> str | None:
        """WhatsApp Cloud's GET webhook handshake.

        Returns the value of `hub.challenge` only when mode=subscribe AND
        the verify token matches what's configured. Returns None when the
        request is not a challenge at all (caller returns 404). Raises
        SignatureError when mode IS subscribe but the token is wrong
        (caller returns 401)."""
        if params.get("hub.mode") != "subscribe":
            return None
        if params.get("hub.verify_token") != self._verify_token:
            raise SignatureError("verify token mismatch")
        return params.get("hub.challenge")

    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        raise NotImplementedError("Lands in Plano 5 Task 14")

    async def send_text(self, to: str, text: str) -> SendResult:
        raise NotImplementedError("Lands in Plano 5 Task 15")


# Replace the placeholder builder registered in Task 12.
# We re-register here; the factory's _REGISTRY mutates.
from ai_sdr.messaging import factory as _factory_module  # noqa: E402

_factory_module._REGISTRY.pop("whatsapp_cloud", None)


@register_provider("whatsapp_cloud")
def _build_whatsapp_cloud(
    cfg: MessagingConfig, secrets: Mapping[str, str]
) -> MessagingAdapter:
    return WhatsAppCloudAPIAdapter(cfg, secrets)
```

- [ ] **Step 4: Update factory test to reflect new behavior**

The test `test_factory_builds_whatsapp_cloud_with_secrets` in `tests/unit/test_messaging_factory.py` (added in Task 12) currently asserts `NotImplementedError`. Update it to assert successful construction:

```python
def test_factory_builds_whatsapp_cloud_with_secrets() -> None:
    """After Task 13, the real WhatsAppCloudAPIAdapter is constructed."""
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "111", "wa_token": "EAA...",
        "wa_verify": "vt", "wa_app_secret": "as",
    }
    a = build_messaging_adapter(cfg, secrets=secrets)
    from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
    assert isinstance(a, WhatsAppCloudAPIAdapter)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_whatsapp_challenge.py tests/unit/test_messaging_factory.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/whatsapp_cloud.py tests/unit/test_whatsapp_challenge.py tests/unit/test_messaging_factory.py
git commit -m "feat(plan5 t13): WhatsAppCloudAPIAdapter scaffold + verification_challenge"
```

---

## Task 14: WhatsApp adapter — `handle_inbound` (HMAC + parser)

**Files:**
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Create: `tests/fixtures/whatsapp/inbound_text.json`
- Create: `tests/fixtures/whatsapp/inbound_status_update.json`
- Create: `tests/fixtures/whatsapp/inbound_image.json`
- Create: `tests/unit/test_whatsapp_handle_inbound.py`

**Design:** Verify `X-Hub-Signature-256` with `hmac.compare_digest` using SHA-256 + `app_secret`. Parse the WhatsApp Cloud webhook envelope `{entry: [{changes: [{value: {messages: [...]}}]}]}`. For each `messages[]` entry with `type=='text'`, normalize to an `InboundMessage`. Skip non-text types (audio/image/document — Plano 8 will pick these up). Skip status updates (`value.statuses` present, no `messages`). The `from` field comes without a `+` prefix; we prepend it.

- [ ] **Step 1: Create the test fixtures**

Create `tests/fixtures/whatsapp/inbound_text.json`:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "5511999999999",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "contacts": [
              {
                "profile": {"name": "Lead Name"},
                "wa_id": "5511988887777"
              }
            ],
            "messages": [
              {
                "from": "5511988887777",
                "id": "wamid.HBgM_FIRSTMESSAGE_AAAA=",
                "timestamp": "1748169600",
                "text": {"body": "oi, queria saber sobre a mentoria"},
                "type": "text"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

Create `tests/fixtures/whatsapp/inbound_status_update.json`:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "5511999999999",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "statuses": [
              {
                "id": "wamid.HBgM_OUTBOUND_AAAA=",
                "status": "delivered",
                "timestamp": "1748169605",
                "recipient_id": "5511988887777"
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

Create `tests/fixtures/whatsapp/inbound_image.json`:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "5511999999999",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "messages": [
              {
                "from": "5511988887777",
                "id": "wamid.HBgM_IMAGE_AAAA=",
                "timestamp": "1748169610",
                "type": "image",
                "image": {
                  "mime_type": "image/jpeg",
                  "sha256": "abc...",
                  "id": "MEDIA_ID"
                }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_whatsapp_handle_inbound.py`:

```python
"""handle_inbound: HMAC verify + payload normalize. Uses real-shaped fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


def _adapter(app_secret: str = "test_app_secret") -> WhatsAppCloudAPIAdapter:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999111", "wa_token": "EAA...",
        "wa_verify": "vt", "wa_app_secret": app_secret,
    }
    return WhatsAppCloudAPIAdapter(cfg, secrets)


def _sign(body: bytes, secret: str = "test_app_secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def test_raises_signature_error_on_missing_header() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    with pytest.raises(SignatureError, match="missing"):
        await a.handle_inbound(body, headers={})


async def test_raises_signature_error_on_bad_signature() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    with pytest.raises(SignatureError, match="HMAC"):
        await a.handle_inbound(
            body, headers={"x-hub-signature-256": "sha256=" + "0" * 64}
        )


async def test_parses_text_message() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert len(msgs) == 1
    m = msgs[0]
    assert m.external_id == "wamid.HBgM_FIRSTMESSAGE_AAAA="
    assert m.from_address == "+5511988887777"
    assert m.text == "oi, queria saber sobre a mentoria"
    assert m.received_at_iso.startswith("2025-")  # 1748169600 → 2025-05-25 (UTC)
    assert m.raw["id"] == "wamid.HBgM_FIRSTMESSAGE_AAAA="


async def test_ignores_status_update_payload() -> None:
    a = _adapter()
    body = _fixture("inbound_status_update.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert msgs == []


async def test_ignores_non_text_message() -> None:
    a = _adapter()
    body = _fixture("inbound_image.json")
    msgs = await a.handle_inbound(body, headers={"x-hub-signature-256": _sign(body)})
    assert msgs == []  # image messages are skipped in Plano 5; Plano 8 picks them up


async def test_header_lookup_is_case_insensitive() -> None:
    a = _adapter()
    body = _fixture("inbound_text.json")
    # Some HTTP frameworks normalize headers to title-case
    msgs = await a.handle_inbound(body, headers={"X-Hub-Signature-256": _sign(body)})
    assert len(msgs) == 1
```

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/unit/test_whatsapp_handle_inbound.py -v`

Expected: FAIL — `handle_inbound` still raises NotImplementedError.

- [ ] **Step 4: Implement `handle_inbound`**

Edit `src/ai_sdr/messaging/whatsapp_cloud.py`. Add imports at the top of the file (just below the existing imports):

```python
import hashlib
import hmac
import json
from datetime import datetime, timezone
```

Replace the `handle_inbound` stub method with the real implementation:

```python
    async def handle_inbound(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        # Header lookup is case-insensitive — uvicorn lowercases, but tests
        # and proxies may not, so we normalize.
        sig_header = next(
            (v for k, v in headers.items() if k.lower() == "x-hub-signature-256"),
            "",
        )
        if not sig_header.startswith("sha256="):
            raise SignatureError(
                "missing or malformed X-Hub-Signature-256 header"
            )
        expected = "sha256=" + hmac.new(
            self._app_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig_header):
            raise SignatureError("HMAC mismatch")

        payload = json.loads(raw_body)
        out: list[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # Status updates have `statuses` but no `messages`.
                for m in value.get("messages", []):
                    if m.get("type") != "text":
                        continue  # Plano 5: text only. Audio/image come in Plano 8.
                    text_body = (m.get("text") or {}).get("body", "")
                    received_dt = datetime.fromtimestamp(
                        int(m["timestamp"]), tz=timezone.utc
                    )
                    out.append(
                        InboundMessage(
                            external_id=m["id"],
                            from_address="+" + m["from"],
                            text=text_body,
                            received_at_iso=received_dt.isoformat(),
                            raw=m,
                        )
                    )
        return out
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_whatsapp_handle_inbound.py -v`

Expected: all six tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/whatsapp_cloud.py tests/fixtures/whatsapp/ tests/unit/test_whatsapp_handle_inbound.py
git commit -m "feat(plan5 t14): WhatsApp adapter handle_inbound — HMAC verify + parser"
```

---

## Task 15: WhatsApp adapter — `send_text` with retry + error classification

**Files:**
- Modify: `src/ai_sdr/messaging/whatsapp_cloud.py`
- Create: `tests/unit/test_whatsapp_send_text.py`

**Design:** Use `httpx.AsyncClient` to POST to `https://graph.facebook.com/{api_version}/{phone_number_id}/messages` with the bearer token. Wrap in `tenacity.AsyncRetrying` with 3 attempts, exponential backoff (1s, 2s, 4s), retry only on `TransientError`/`RateLimitError`. Map Meta error codes to typed exceptions:

| HTTP / error_code | → Exception |
|---|---|
| 401 / 403 / error 190 | `AuthError` |
| 400, error 131026 (recipient not on WA), 131051 (unsupported msg type back-ref) | `RecipientUnreachable` |
| 400, error 131047 (24h window) | `WindowExpiredError` |
| 400, error 131048 (rate limit hit), 131049 (policy) | `PolicyError` |
| 429 (any code) | `RateLimitError(retry_after=Retry-After header or 60)` |
| 5xx, timeout, network error | `TransientError` |
| other 4xx | `PolicyError` (conservative catch-all) |

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_whatsapp_send_text.py`:

```python
"""send_text: error classification table + happy path + retry behavior.

Uses httpx's mock transport to drive deterministic responses without a
real network. The retry logic is tested with deterministic sleep (we
patch tenacity's wait time to zero so the test runs instantly).
"""

from __future__ import annotations

import json

import httpx
import pytest

from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    TransientError,
    WindowExpiredError,
)
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig


@pytest.fixture
def adapter_no_retry_sleep(monkeypatch) -> WhatsAppCloudAPIAdapter:
    """Adapter with retry wait patched to 0s for deterministic test runs."""
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
    # Patch the wait_strategy to zero so retries are instantaneous.
    import tenacity
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY",
        tenacity.wait_none(),
    )
    return a


def _mount(client_response: httpx.Response):
    """Return a Transport that returns the given response."""
    return httpx.MockTransport(lambda request: client_response)


def _ok_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+5511999999999", "wa_id": "5511999999999"}],
            "messages": [{"id": "wamid.OUT_SENT_AAAA="}],
        },
    )


def _error_response(status: int, code: int, subcode: int | None = None,
                    message: str = "err",
                    extra_headers: dict[str, str] | None = None) -> httpx.Response:
    error: dict[str, object] = {"code": code, "message": message}
    if subcode is not None:
        error["error_subcode"] = subcode
    return httpx.Response(
        status,
        json={"error": error},
        headers=extra_headers or {},
    )


async def test_send_text_happy_path(adapter_no_retry_sleep, monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=_mount(_ok_response()), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "hello")
    assert r.external_id == "wamid.OUT_SENT_AAAA="


@pytest.mark.parametrize(
    "status, code, expected_exc",
    [
        (401, 190, AuthError),
        (403, 190, AuthError),
        (400, 131026, RecipientUnreachable),
        (400, 131051, RecipientUnreachable),
        (400, 131047, WindowExpiredError),
        (400, 131048, PolicyError),
        (400, 131049, PolicyError),
        (400, 999999, PolicyError),  # unknown 4xx → conservative PolicyError
    ],
)
async def test_send_text_classifies_terminal_errors(
    adapter_no_retry_sleep, monkeypatch, status, code, expected_exc
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=_mount(_error_response(status, code)), timeout=15.0
        ),
    )
    with pytest.raises(expected_exc):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")


async def test_send_text_rate_limit_is_retried_then_succeeds(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    responses = iter([
        _error_response(429, code=4, extra_headers={"Retry-After": "1"}),
        _ok_response(),
    ])

    def transport(_request):
        return next(responses)

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "x")
    assert r.external_id  # second attempt succeeded


async def test_send_text_rate_limit_exhausted_raises(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    # Three rate-limited responses → tenacity exhausts → raises last exception
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=_mount(_error_response(
                429, code=4, extra_headers={"Retry-After": "1"}
            )),
            timeout=15.0,
        ),
    )
    with pytest.raises(RateLimitError):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")


async def test_send_text_5xx_is_classified_transient_and_retried(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    responses = iter([
        _error_response(503, code=2, message="service unavailable"),
        _error_response(503, code=2, message="service unavailable"),
        _ok_response(),
    ])

    def transport(_request):
        return next(responses)

    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(transport), timeout=15.0),
    )
    r = await adapter_no_retry_sleep.send_text("+5511999999999", "x")
    assert r.external_id  # third attempt succeeded


async def test_send_text_5xx_exhausted_raises_transient(
    adapter_no_retry_sleep, monkeypatch
) -> None:
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._build_http_client",
        lambda: httpx.AsyncClient(
            transport=_mount(_error_response(503, code=2)), timeout=15.0
        ),
    )
    with pytest.raises(TransientError):
        await adapter_no_retry_sleep.send_text("+5511999999999", "x")
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_whatsapp_send_text.py -v`

Expected: FAIL with NotImplementedError or AttributeError on `_WAIT_STRATEGY` / `_build_http_client`.

- [ ] **Step 3: Implement `send_text` + helpers**

Edit `src/ai_sdr/messaging/whatsapp_cloud.py`. Add to the top-of-file imports:

```python
import logging

import httpx
import tenacity
import structlog

from ai_sdr.messaging.errors import (
    AuthError,
    PolicyError,
    RateLimitError,
    RecipientUnreachable,
    TransientError,
    WindowExpiredError,
)
```

Add these module-level constants and helpers below the existing imports:

```python
log = structlog.get_logger(__name__)

# Tenacity wait strategy is exposed at module level so tests can monkeypatch
# it to zero. Production wait is exponential 1s, 2s, 4s.
_WAIT_STRATEGY = tenacity.wait_exponential(multiplier=1, min=1, max=4)
_MAX_ATTEMPTS = 3


def _build_http_client() -> httpx.AsyncClient:
    """Factory hook — tests patch this to inject a mock transport."""
    return httpx.AsyncClient(timeout=15.0)


def _classify_error(
    status: int, error: dict[str, object] | None, retry_after_s: int | None
) -> Exception:
    """Map a (status, error body) pair to one of our typed exceptions."""
    code = (error or {}).get("code")

    if status in (401, 403) or code == 190:
        return AuthError(f"WhatsApp auth error: {error!r}")
    if status == 400:
        if code == 131026 or code == 131051:
            return RecipientUnreachable(f"recipient unreachable: {error!r}")
        if code == 131047:
            return WindowExpiredError(f"24h window expired: {error!r}")
        if code in (131048, 131049):
            return PolicyError(f"policy violation: {error!r}")
        # Conservative catch-all for unknown 4xx — alert ops.
        return PolicyError(f"unknown 4xx: status={status} body={error!r}")
    if status == 429:
        return RateLimitError(retry_after_s=retry_after_s or 60)
    if 500 <= status < 600:
        return TransientError(f"5xx from WhatsApp: status={status} body={error!r}")
    return TransientError(f"unexpected status {status}: {error!r}")
```

Add the `send_text` implementation, replacing the NotImplementedError stub. The method body must wrap the HTTP call in tenacity's AsyncRetrying. Place this inside the `WhatsAppCloudAPIAdapter` class:

```python
    async def send_text(self, to: str, text: str) -> SendResult:
        url = (
            f"https://graph.facebook.com/{self._api_version}/"
            f"{self._phone_number_id}/messages"
        )
        body = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": text},
        }
        request_headers = {"Authorization": f"Bearer {self._access_token}"}

        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(_MAX_ATTEMPTS),
            wait=_WAIT_STRATEGY,
            retry=tenacity.retry_if_exception_type(TransientError),
            reraise=True,
        )

        log.info("wa.send.start", to=to, attempts_max=_MAX_ATTEMPTS)
        async for attempt in retryer:
            with attempt:
                async with _build_http_client() as client:
                    response = await client.post(url, json=body, headers=request_headers)
                if response.status_code == 200:
                    data = response.json()
                    out_id = data["messages"][0]["id"]
                    log.info(
                        "wa.send.success",
                        to=to,
                        external_id=out_id,
                        attempt=attempt.retry_state.attempt_number,
                    )
                    return SendResult(
                        external_id=out_id,
                        sent_at_iso=datetime.now(timezone.utc).isoformat(),
                    )
                # Non-200: classify, raise (tenacity decides retry vs terminal).
                try:
                    err_body = response.json().get("error")
                except Exception:
                    err_body = None
                retry_after_hdr = response.headers.get("Retry-After")
                retry_after_s = int(retry_after_hdr) if retry_after_hdr else None
                exc = _classify_error(response.status_code, err_body, retry_after_s)
                log.warning(
                    "wa.send.error",
                    to=to,
                    status=response.status_code,
                    err_type=type(exc).__name__,
                    err=str(exc),
                    attempt=attempt.retry_state.attempt_number,
                )
                raise exc

        # Unreachable: tenacity reraises on exhaustion via reraise=True.
        raise RuntimeError("unreachable: tenacity exhausted without raising")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_whatsapp_send_text.py -v`

Expected: all parametrized + non-parametrized tests PASS.

- [ ] **Step 5: Sanity-check full unit suite hasn't regressed**

Run: `uv run pytest tests/unit/ -v -x`

Expected: all PASS. If a test from Plano 1/2/3 fails because of the Lead/TalkFlow type change in earlier tasks, fix it locally to create a Lead row first.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/whatsapp_cloud.py tests/unit/test_whatsapp_send_text.py
git commit -m "feat(plan5 t15): WhatsApp send_text with tenacity retry + error taxonomy"
```

---

## Task 16: `messaging/ingest.py` — `find_or_create_lead_by_address` + `ingest_inbound_message`

**Files:**
- Create: `src/ai_sdr/messaging/ingest.py`
- Create: `tests/integration/test_messaging_ingest.py`

**Design:** Two helpers. `find_or_create_lead_by_address(db, tenant_id, provider, address) → Lead` does an idempotent lookup by `(tenant_id, whatsapp_e164=address)` and creates a new Lead with `status='pending_assignment'` if not found. The function asserts `provider == 'whatsapp_cloud'` for Plano 5 (Plano 6 generalizes via `IdentityResolver`). `ingest_inbound_message(db, tenant, provider, msg) → IngestResult` does the `ON CONFLICT DO NOTHING` insert and returns `IngestResult(status, lead_id)`. Both functions assume the caller has already called `set_tenant_context` and will `await db.commit()` afterward — they do NOT commit themselves.

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_messaging_ingest.py`:

```python
"""ingest helpers — find-or-create lead + dedupe inbound."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.base import InboundMessage
from ai_sdr.messaging.ingest import (
    find_or_create_lead_by_address,
    ingest_inbound_message,
)
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def _make_tenant(session) -> Tenant:
    t = Tenant(slug=f"t_{uuid.uuid4().hex[:6]}", display_name="T")
    session.add(t)
    await session.flush()
    return t


async def test_find_or_create_creates_pending_lead(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    lead = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    await db_session.commit()
    assert lead.status == "pending_assignment"
    assert lead.whatsapp_e164 == "+5511999999999"


async def test_find_or_create_returns_existing_lead(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    first = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    await db_session.commit()

    second = await find_or_create_lead_by_address(
        db_session, tenant.id, "whatsapp_cloud", "+5511999999999"
    )
    assert second.id == first.id


async def test_ingest_inbound_inserts_then_dedupes(db_session) -> None:
    tenant = await _make_tenant(db_session)
    await db_session.commit()
    await set_tenant_context(db_session, tenant.id)

    msg = InboundMessage(
        external_id="wamid.ABC",
        from_address="+5511999999999",
        text="oi",
        received_at_iso=datetime.now(timezone.utc).isoformat(),
        raw={"id": "wamid.ABC"},
    )

    r1 = await ingest_inbound_message(db_session, tenant, "whatsapp_cloud", msg)
    await db_session.commit()
    assert r1.status == "queued"

    r2 = await ingest_inbound_message(db_session, tenant, "whatsapp_cloud", msg)
    await db_session.commit()
    assert r2.status == "skipped_dedupe"
    assert r2.lead_id == r1.lead_id

    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_messaging_ingest.py -v`

Expected: FAIL — `ImportError: cannot import name 'find_or_create_lead_by_address'`.

- [ ] **Step 3: Create the ingest module**

Create `src/ai_sdr/messaging/ingest.py`:

```python
"""Persist inbound messages + resolve sender to a Lead.

Both helpers leave commit() to the caller; they only flush. Tenant context
must be set by the caller via set_tenant_context() — these helpers do not
escalate privileges.

The provider-dispatching find_or_create_lead_by_address() is Plano 5's
ad-hoc Identity boundary. Plano 6 promotes it to an `IdentityResolver`
interface and adds a Vialum impl; the WhatsApp behavior here becomes the
default 'InternalLead' implementation — no signature change at the call
sites.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.messaging.base import InboundMessage
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant


@dataclass(frozen=True)
class IngestResult:
    status: Literal["queued", "skipped_dedupe"]
    lead_id: uuid.UUID


async def find_or_create_lead_by_address(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    provider: str,
    address: str,
) -> Lead:
    """Return the lead with this provider-native address, creating one with
    status='pending_assignment' if needed.

    Plano 5 only supports `whatsapp_cloud` — the address is stored in
    `leads.whatsapp_e164`. Other providers raise NotImplementedError until
    Plano 6 generalizes the identity layer."""
    if provider != "whatsapp_cloud":
        raise NotImplementedError(
            f"find_or_create_lead_by_address: provider {provider!r} "
            "is not supported until Plano 6 (Identity)"
        )

    existing = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.whatsapp_e164 == address,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    lead = Lead(
        tenant_id=tenant_id,
        whatsapp_e164=address,
        status="pending_assignment",
    )
    session.add(lead)
    await session.flush()
    return lead


async def ingest_inbound_message(
    session: AsyncSession,
    tenant: Tenant,
    provider: str,
    msg: InboundMessage,
) -> IngestResult:
    """Resolve sender → lead, then INSERT ... ON CONFLICT DO NOTHING the
    inbound row. Returns IngestResult so the caller knows whether to
    enqueue work or not."""
    lead = await find_or_create_lead_by_address(
        session, tenant.id, provider, msg.from_address
    )

    received_at = datetime.fromisoformat(msg.received_at_iso)
    stmt = (
        pg_insert(InboundMessageRow)
        .values(
            tenant_id=tenant.id,
            provider=provider,
            external_id=msg.external_id,
            lead_id=lead.id,
            from_address=msg.from_address,
            text=msg.text,
            received_at=received_at,
            raw=dict(msg.raw),
            status="queued",
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "provider", "external_id"]
        )
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        return IngestResult(status="skipped_dedupe", lead_id=lead.id)
    return IngestResult(status="queued", lead_id=lead.id)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_messaging_ingest.py -v`

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/messaging/ingest.py tests/integration/test_messaging_ingest.py
git commit -m "feat(plan5 t16): ingest helpers — find_or_create_lead + dedupe insert"
```

---

## Task 17: `messaging/registry.py` — adapter cache

**Files:**
- Create: `src/ai_sdr/messaging/registry.py`
- Create: `tests/unit/test_messaging_registry.py`

**Design:** A small singleton-style cache for `(tenant_id, provider) → MessagingAdapter`. First lookup loads tenant config + secrets and calls the factory; subsequent lookups return the cached instance. Cache is reset by calling `clear()` (used in tests). Adapter rebuilds happen at process restart — Plano 5 doesn't implement filewatch (that's a dev nicety; production restarts on config change).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_messaging_registry.py`:

```python
"""AdapterRegistry caches adapter instances per (tenant_id, provider)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.schemas.tenant_yaml import MessagingConfig


def _stub_loader(provider: str = "fake"):
    """Returns (tenant_loader_mock, sops_loader_mock) wired with stubs."""
    tenant_cfg = MagicMock()
    tenant_cfg.messaging = MessagingConfig(provider=provider)
    tenant_loader = MagicMock()
    tenant_loader.load_by_id.return_value = tenant_cfg

    sops_loader = MagicMock()
    sops_loader.load_by_id.return_value = {}

    return tenant_loader, sops_loader


def test_first_lookup_builds_adapter() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)

    tenant_id = uuid.uuid4()
    a = registry.get(tenant_id, "fake")
    assert isinstance(a, FakeMessagingAdapter)


def test_second_lookup_returns_cached_instance() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant_id = uuid.uuid4()
    a = registry.get(tenant_id, "fake")
    b = registry.get(tenant_id, "fake")
    assert a is b
    # tenant_loader was called exactly once
    assert tenant_loader.load_by_id.call_count == 1


def test_clear_resets_cache() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant_id = uuid.uuid4()
    a = registry.get(tenant_id, "fake")
    registry.clear()
    b = registry.get(tenant_id, "fake")
    assert a is not b
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/unit/test_messaging_registry.py -v`

Expected: FAIL with `ImportError: cannot import name 'AdapterRegistry'`.

- [ ] **Step 3: Create the registry**

Create `src/ai_sdr/messaging/registry.py`:

```python
"""Per-process cache of MessagingAdapter instances.

The factory is cheap (just constructor + secrets dict), but resolving
secrets requires SOPS decryption — expensive on cold path. Caching
(tenant_id, provider) → adapter avoids re-decrypting on every webhook.

This is a *singleton-style* registry per process; in the API layer it's
stored on app.state; in the worker process it's a module-level instance.
"""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.factory import build_messaging_adapter

if TYPE_CHECKING:
    from ai_sdr.secrets.sops_loader import SopsLoader
    from ai_sdr.tenant_loader.loader import TenantLoader


class AdapterRegistry:
    """Thread-safe cache of MessagingAdapter instances."""

    def __init__(
        self,
        tenant_loader: "TenantLoader",
        sops_loader: "SopsLoader",
    ) -> None:
        self._tenant_loader = tenant_loader
        self._sops_loader = sops_loader
        self._cache: dict[tuple[uuid.UUID, str], MessagingAdapter] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: uuid.UUID, provider: str) -> MessagingAdapter:
        key = (tenant_id, provider)
        with self._lock:
            adapter = self._cache.get(key)
            if adapter is not None:
                return adapter

            tenant_cfg = self._tenant_loader.load_by_id(tenant_id)
            if tenant_cfg.messaging is None:
                raise ValueError(
                    f"tenant {tenant_id} has no `messaging` block in tenant.yaml"
                )
            if tenant_cfg.messaging.provider != provider:
                raise ValueError(
                    f"tenant {tenant_id} configured provider="
                    f"{tenant_cfg.messaging.provider!r} but received {provider!r}"
                )
            secrets = self._sops_loader.load_by_id(tenant_id)
            adapter = build_messaging_adapter(tenant_cfg.messaging, secrets)
            self._cache[key] = adapter
            return adapter

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
```

- [ ] **Step 4: Add the loader-by-id helpers if they don't already exist**

Check whether `TenantLoader.load_by_id(uuid.UUID)` and `SopsLoader.load_by_id(uuid.UUID)` exist. They likely don't — the existing methods are slug-based (`load(slug)`). Search:

```bash
grep -n "def load" src/ai_sdr/tenant_loader/loader.py src/ai_sdr/secrets/sops_loader.py
```

If only `load(slug)` exists, add `load_by_id(tenant_id)` to each. The TenantLoader is in-process and has no DB awareness — so `load_by_id` needs the slug. The cleanest approach: thread a DB lookup through the registry. Refactor `AdapterRegistry.get` to take a `Tenant` ORM object instead of a `uuid.UUID`, and let the route handler do the DB lookup once:

Replace `def get(self, tenant_id: uuid.UUID, provider: str)` with:

```python
    def get(self, tenant: "Tenant", provider: str) -> MessagingAdapter:
        key = (tenant.id, provider)
        with self._lock:
            adapter = self._cache.get(key)
            if adapter is not None:
                return adapter

            tenant_cfg = self._tenant_loader.load(tenant.slug)
            if tenant_cfg.messaging is None:
                raise ValueError(
                    f"tenant {tenant.slug} has no `messaging` block in tenant.yaml"
                )
            if tenant_cfg.messaging.provider != provider:
                raise ValueError(
                    f"tenant {tenant.slug} configured provider="
                    f"{tenant_cfg.messaging.provider!r} but received {provider!r}"
                )
            secrets = self._sops_loader.load(tenant.slug)
            adapter = build_messaging_adapter(tenant_cfg.messaging, secrets)
            self._cache[key] = adapter
            return adapter
```

Add `from ai_sdr.models.tenant import Tenant` to the `TYPE_CHECKING` block.

Update the test to use a Tenant mock instead of a raw UUID:

```python
def _stub_loader(provider: str = "fake"):
    tenant_cfg = MagicMock()
    tenant_cfg.messaging = MessagingConfig(provider=provider)
    tenant_loader = MagicMock()
    tenant_loader.load.return_value = tenant_cfg

    sops_loader = MagicMock()
    sops_loader.load.return_value = {}

    return tenant_loader, sops_loader


def _make_tenant(slug: str = "t") -> MagicMock:
    t = MagicMock()
    t.id = uuid.uuid4()
    t.slug = slug
    return t


def test_first_lookup_builds_adapter() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    assert isinstance(a, FakeMessagingAdapter)


def test_second_lookup_returns_cached_instance() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    b = registry.get(tenant, "fake")
    assert a is b
    assert tenant_loader.load.call_count == 1


def test_clear_resets_cache() -> None:
    tenant_loader, sops_loader = _stub_loader("fake")
    registry = AdapterRegistry(tenant_loader=tenant_loader, sops_loader=sops_loader)
    tenant = _make_tenant()
    a = registry.get(tenant, "fake")
    registry.clear()
    b = registry.get(tenant, "fake")
    assert a is not b
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_messaging_registry.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/messaging/registry.py tests/unit/test_messaging_registry.py
git commit -m "feat(plan5 t17): AdapterRegistry — per-process (tenant, provider) cache"
```

---

## Task 18: Webhook routes — `GET /challenge` + `POST /ingest`

**Files:**
- Create: `src/ai_sdr/api/routes/webhooks.py`
- Modify: `src/ai_sdr/main.py`
- Modify: `src/ai_sdr/api/deps.py`
- Create: `tests/integration/test_webhook_routes.py`

**Design:** Two endpoints. `GET /webhooks/{tenant_slug}/{provider}` resolves the tenant and adapter, calls `adapter.verification_challenge(query_params)`, and either returns the challenge as text (200) or 404 (when None). `POST /webhooks/{tenant_slug}/{provider}` resolves the same, calls `adapter.handle_inbound(raw_body, headers)`, and for each returned `InboundMessage` calls `ingest_inbound_message`. Successful inserts trigger one arq job per affected lead. `SignatureError` → HTTP 401.

The arq pool is stored on `app.state` and reached via a dep. Same for the adapter registry.

- [ ] **Step 1: Add arq_pool dep + adapter_registry to app.state**

First, modify `src/ai_sdr/main.py` to bring up an arq Redis pool and an AdapterRegistry at startup, store them on app.state, and tear down on shutdown. Replace the lifespan + create_app section:

```python
"""FastAPI app entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI

from ai_sdr.api.routes.health import router as health_router
from ai_sdr.api.routes.webhooks import router as webhooks_router
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("app.starting", env=settings.app_env)
    await ensure_checkpointer_schema()
    log.info("checkpointer.ready")

    app.state.arq_pool = await create_pool(
        RedisSettings.from_dsn(settings.redis_url)
    )
    tenants_dir = Path(settings.tenants_dir)
    app.state.adapter_registry = AdapterRegistry(
        tenant_loader=TenantLoader(tenants_dir),
        sops_loader=SopsLoader(tenants_dir),
    )
    log.info("messaging.ready")

    yield
    await app.state.arq_pool.aclose()
    log.info("app.stopping")


def create_app() -> FastAPI:
    app = FastAPI(title="AI SDR", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(webhooks_router)
    return app


app = create_app()
```

Add deps in `src/ai_sdr/api/deps.py`. Append to the file:

```python
from fastapi import Request
from arq.connections import ArqRedis

from ai_sdr.messaging.registry import AdapterRegistry


def arq_pool(request: Request) -> ArqRedis:
    """Returns the per-process arq pool created at startup (see main.py)."""
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise RuntimeError("arq_pool not initialized (lifespan didn't run)")
    return pool


def adapter_registry(request: Request) -> AdapterRegistry:
    """Returns the per-process AdapterRegistry created at startup."""
    reg = getattr(request.app.state, "adapter_registry", None)
    if reg is None:
        raise RuntimeError("adapter_registry not initialized (lifespan didn't run)")
    return reg
```

- [ ] **Step 2: Write the failing test**

Create `tests/integration/test_webhook_routes.py`:

```python
"""Webhook routes — challenge handshake, signature failure, ingest happy path."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.schemas.tenant_yaml import MessagingConfig

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


@pytest.fixture
async def example_tenant_in_db(db_session) -> Tenant:
    t = Tenant(slug=f"webhk_{uuid.uuid4().hex[:6]}", display_name="Hook Tenant")
    db_session.add(t)
    await db_session.commit()
    return t


@pytest.fixture
def configured_app(app, monkeypatch, example_tenant_in_db):
    """Wire app.state with a tenant whose messaging.provider=='whatsapp_cloud'
    but whose AdapterRegistry returns a FakeMessagingAdapter (so we don't
    need real secrets). The registry's stub also accepts any HMAC signature
    for inbound messages (because Fake.verify is trivial).

    Wait — actually for these tests we want to exercise the WhatsApp HMAC
    path, so we mount a real WhatsAppCloudAPIAdapter wired with known
    secrets, and sign payloads with the matching key. See `signed_app`."""
    return app


@pytest.fixture
def signed_app(app, monkeypatch, example_tenant_in_db, db_session):
    """Mount an AdapterRegistry that returns a real WhatsAppCloudAPIAdapter
    seeded with known test secrets."""
    from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter

    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999", "wa_token": "EAA",
        "wa_verify": "verify_token_42", "wa_app_secret": "app_secret_xyz",
    }
    adapter = WhatsAppCloudAPIAdapter(cfg, secrets)

    class StaticRegistry:
        def get(self, tenant, provider):
            return adapter

    app.state.adapter_registry = StaticRegistry()
    return app


async def test_get_challenge_echoes_when_token_matches(
    signed_app, example_tenant_in_db
) -> None:
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.get(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify_token_42",
                "hub.challenge": "challenge_payload",
            },
        )
    assert r.status_code == 200
    assert r.text == "challenge_payload"


async def test_get_challenge_401_when_token_mismatch(
    signed_app, example_tenant_in_db
) -> None:
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.get(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG",
                "hub.challenge": "x",
            },
        )
    assert r.status_code == 401


async def test_post_returns_401_on_bad_signature(
    signed_app, example_tenant_in_db
) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": "sha256=" + "0" * 64},
        )
    assert r.status_code == 401


async def test_post_ingests_and_enqueues(
    signed_app, example_tenant_in_db, db_session, monkeypatch
) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(
        b"app_secret_xyz", body, hashlib.sha256
    ).hexdigest()

    enqueued = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args, kwargs))
            return None

    signed_app.state.arq_pool = FakePool()

    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body,
            headers={"x-hub-signature-256": sig},
        )
    assert r.status_code == 200

    from ai_sdr.db.rls import set_tenant_context
    await set_tenant_context(db_session, example_tenant_in_db.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "oi, queria saber sobre a mentoria"

    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert len(leads) == 1
    assert leads[0].status == "pending_assignment"
    assert leads[0].whatsapp_e164 == "+5511988887777"

    # One job enqueued for the affected lead
    assert len(enqueued) == 1
    name, args, _ = enqueued[0]
    assert name == "process_lead_inbox"
    assert args == (str(example_tenant_in_db.id), str(leads[0].id))


async def test_post_idempotent_on_duplicate_external_id(
    signed_app, example_tenant_in_db, db_session
) -> None:
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(
        b"app_secret_xyz", body, hashlib.sha256
    ).hexdigest()

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            return None

    signed_app.state.arq_pool = FakePool()

    async with AsyncClient(app=signed_app, base_url="http://test") as client:
        r1 = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body, headers={"x-hub-signature-256": sig},
        )
        r2 = await client.post(
            f"/webhooks/{example_tenant_in_db.slug}/whatsapp_cloud",
            content=body, headers={"x-hub-signature-256": sig},
        )
    assert r1.status_code == 200
    assert r2.status_code == 200

    from ai_sdr.db.rls import set_tenant_context
    await set_tenant_context(db_session, example_tenant_in_db.id)
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert len(rows) == 1  # second was deduped
```

The `app` fixture is the existing test fixture from prior plans. If a `signed_app` clashes — adjust naming in conftest.

- [ ] **Step 3: Run (expect fail)**

Run: `uv run pytest tests/integration/test_webhook_routes.py -v`

Expected: FAIL — webhook routes module does not exist; route returns 404.

- [ ] **Step 4: Create the webhooks router**

Create `src/ai_sdr/api/routes/webhooks.py`:

```python
"""Inbound webhook routes.

URL shape: /webhooks/{tenant_slug}/{provider}

  - GET  → adapter.verification_challenge(query_params) — handshake.
  - POST → adapter.handle_inbound(raw_body, headers), then per InboundMessage:
            ingest_inbound_message → enqueue one job per affected lead.

SignatureError → 401. Unknown tenant or provider mismatch → 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import adapter_registry, arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import SignatureError
from ai_sdr.messaging.ingest import ingest_inbound_message
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.models.tenant import Tenant

log = structlog.get_logger(__name__)
router = APIRouter()


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return tenant


@router.get("/webhooks/{tenant_slug}/{provider}")
async def webhook_challenge(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
) -> Response:
    tenant = await _load_tenant(db, tenant_slug)
    try:
        adapter = registry.get(tenant, provider)
    except ValueError as e:
        # Provider mismatch or no messaging block → 404
        raise HTTPException(status_code=404, detail=str(e)) from e

    try:
        challenge = adapter.verification_challenge(dict(request.query_params))
    except SignatureError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    if challenge is None:
        raise HTTPException(status_code=404, detail="no challenge expected")
    return PlainTextResponse(challenge)


@router.post("/webhooks/{tenant_slug}/{provider}")
async def webhook_ingest(
    tenant_slug: str,
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(db_session)],
    registry: Annotated[AdapterRegistry, Depends(adapter_registry)],
    pool: Annotated[ArqRedis, Depends(arq_pool)],
) -> Response:
    tenant = await _load_tenant(db, tenant_slug)
    try:
        adapter = registry.get(tenant, provider)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    raw_body = await request.body()
    headers = dict(request.headers)
    try:
        messages = await adapter.handle_inbound(raw_body, headers)
    except SignatureError as e:
        log.warning("webhook.signature_error", slug=tenant_slug, err=str(e))
        raise HTTPException(status_code=401, detail="invalid signature") from e

    if not messages:
        return Response(status_code=200)

    await set_tenant_context(db, tenant.id)
    affected_lead_ids: set[uuid.UUID] = set()
    for msg in messages:
        result = await ingest_inbound_message(db, tenant, provider, msg)
        if result.status == "queued":
            affected_lead_ids.add(result.lead_id)
    await db.commit()

    for lead_id in affected_lead_ids:
        await pool.enqueue_job(
            "process_lead_inbox", str(tenant.id), str(lead_id)
        )

    log.info(
        "webhook.ingested",
        slug=tenant_slug,
        provider=provider,
        n_messages=len(messages),
        n_enqueued=len(affected_lead_ids),
    )
    return Response(status_code=200)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/integration/test_webhook_routes.py -v`

Expected: all five tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/api/routes/webhooks.py src/ai_sdr/main.py src/ai_sdr/api/deps.py tests/integration/test_webhook_routes.py
git commit -m "feat(plan5 t18): /webhooks/{tenant}/{provider} GET challenge + POST ingest"
```

---

## Task 19: `worker/main.py` + `ai-sdr worker` CLI

**Files:**
- Create: `src/ai_sdr/worker/__init__.py`
- Create: `src/ai_sdr/worker/main.py`
- Create: `src/ai_sdr/worker/jobs/__init__.py`
- Create: `src/ai_sdr/cli/worker.py`
- Modify: `src/ai_sdr/cli/app.py`

**Design:** `worker/main.py` defines `WorkerSettings` (arq's contract). At startup the worker connects to Redis + ensures the checkpointer schema (mirroring the API's lifespan), instantiates a process-wide AdapterRegistry, and stashes both on `ctx`. The CLI command `ai-sdr worker` invokes `arq.worker.run_worker` with the WorkerSettings class.

- [ ] **Step 1: Create worker package skeleton**

Create empty modules:

```bash
touch src/ai_sdr/worker/__init__.py
touch src/ai_sdr/worker/jobs/__init__.py
```

- [ ] **Step 2: Create `worker/main.py`**

Create `src/ai_sdr/worker/main.py`:

```python
"""arq WorkerSettings — entrypoint for the `ai-sdr worker` process.

The worker stores shared state (db session factory, adapter registry) on
the arq job context so jobs can reach it without globals. Job functions
are registered in `functions`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from arq.connections import RedisSettings

from ai_sdr.db.engine import build_engine
from ai_sdr.db.session import session_factory_for
from ai_sdr.logging_setup import configure_logging
from ai_sdr.messaging.registry import AdapterRegistry
from ai_sdr.secrets.sops_loader import SopsLoader
from ai_sdr.settings import get_settings
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.checkpointer import ensure_checkpointer_schema
from ai_sdr.worker.jobs.inbound import process_lead_inbox


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level)
    log = structlog.get_logger()
    log.info("worker.starting", env=settings.app_env)
    await ensure_checkpointer_schema()

    ctx["engine"] = build_engine(settings.database_url)
    ctx["session_factory"] = session_factory_for(ctx["engine"])

    tenants_dir = Path(settings.tenants_dir)
    ctx["adapter_registry"] = AdapterRegistry(
        tenant_loader=TenantLoader(tenants_dir),
        sops_loader=SopsLoader(tenants_dir),
    )
    log.info("worker.ready")


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    structlog.get_logger().info("worker.stopped")


class WorkerSettings:
    """arq looks up class attributes by name."""

    functions = [process_lead_inbox]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    max_tries = 3
    job_completion_wait = 30  # seconds before retry after unhandled exception

    @classmethod
    def redis_settings(cls) -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)
```

**Note on `build_engine` and `session_factory_for`:** These are helpers that may not yet exist as named. Check `src/ai_sdr/db/engine.py` and `src/ai_sdr/db/session.py`. If they don't exist with those exact names, look for the equivalent module-level helpers (the existing simulate CLI uses `create_async_engine` and `async_sessionmaker` directly). If needed, add tiny wrapper functions:

In `src/ai_sdr/db/engine.py`, add (if not present):

```python
def build_engine(url: str):
    from sqlalchemy.ext.asyncio import create_async_engine
    return create_async_engine(url, future=True, pool_pre_ping=True)
```

In `src/ai_sdr/db/session.py`, add (if not present):

```python
def session_factory_for(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 3: Create the `ai-sdr worker` CLI**

Create `src/ai_sdr/cli/worker.py`:

```python
"""ai-sdr worker — runs the arq job loop in foreground."""

from __future__ import annotations

import typer

from arq.worker import run_worker


def worker() -> None:
    """Start the arq worker process. Blocks until SIGINT/SIGTERM."""
    from ai_sdr.worker.main import WorkerSettings

    run_worker(WorkerSettings)  # type: ignore[arg-type]
```

- [ ] **Step 4: Register `worker` in the top-level typer app**

Edit `src/ai_sdr/cli/app.py`:

```python
"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.reindex_kb import reindex_kb_app
from ai_sdr.cli.simulate import simulate
from ai_sdr.cli.worker import worker

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)
app.command(name="worker")(worker)
app.add_typer(reindex_kb_app, name="reindex-kb")


if __name__ == "__main__":  # pragma: no cover
    app()
```

- [ ] **Step 5: Smoke-test the CLI command resolves**

Run: `uv run ai-sdr worker --help`

Expected: prints typer help for the command (or arq-style help if run_worker echoes it). Should NOT raise ImportError. If Redis isn't running, just don't actually run the worker — `--help` only validates that the wiring imports cleanly.

If the smoke import fails (likely `ModuleNotFoundError` because `worker/jobs/inbound.py` doesn't exist yet), create the placeholder before Task 20 lands:

```python
# src/ai_sdr/worker/jobs/inbound.py (TEMPORARY placeholder — Task 20 replaces)
async def process_lead_inbox(ctx, tenant_id, lead_id):
    raise NotImplementedError("Lands in Plano 5 Task 20")
```

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/worker/ src/ai_sdr/cli/worker.py src/ai_sdr/cli/app.py src/ai_sdr/db/engine.py src/ai_sdr/db/session.py
git commit -m "feat(plan5 t19): worker process scaffold + ai-sdr worker CLI"
```

---

## Task 20: `process_lead_inbox` job — advisory lock + replay loop + error taxonomy

**Files:**
- Create / overwrite: `src/ai_sdr/worker/jobs/inbound.py`
- Create: `tests/integration/test_worker_process_lead_inbox.py`

**Design:** The job opens a fresh DB session via `ctx["session_factory"]`, acquires a per-lead Postgres advisory lock (`pg_try_advisory_lock(hash)`), and bails if another worker already has it. Then it loads tenant + lead. If lead.status == `pending_assignment` → return (operator hasn't assigned yet). If `unreachable` → mark all queued inbounds for this lead as `error` and return. If `active` → find the talkflow, fetch queued inbounds ordered by `received_at`, and for each: `runtime.step(user_input=text)` → `adapter.send_text(to=from_address)` → update msg.status. Catch the four terminal exceptions per the table in Section 8 of the spec.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_worker_process_lead_inbox.py`:

```python
"""Worker job tests — advisory lock + status transitions + error taxonomy.

These tests construct a real DB session + a FakeMessagingAdapter, then
invoke `process_lead_inbox` directly (no arq runtime needed) by passing
a minimal ctx dict."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.base import SendResult
from ai_sdr.messaging.errors import RecipientUnreachable
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory():
    engine = build_engine(get_settings().database_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return sf


def _ctx(session_factory, adapter, runtime_stub):
    registry = MagicMock()
    registry.get.return_value = adapter
    return {
        "session_factory": session_factory,
        "adapter_registry": registry,
        "runtime": runtime_stub,
    }


async def _setup_tenant_with_lead(db_session, status: str) -> tuple[Tenant, Lead]:
    tenant = Tenant(slug=f"w_{uuid.uuid4().hex[:6]}", display_name="W")
    db_session.add(tenant)
    await db_session.flush()

    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="t1", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: t1\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999999999", status=status)
    db_session.add(lead)
    await db_session.flush()

    if status == "active":
        tf = TalkFlow(
            tenant_id=tenant.id, lead_id=lead.id, treeflow_version_id=tv.id,
            thread_id=f"{tenant.id}:{uuid.uuid4()}",
        )
        db_session.add(tf)
    await db_session.commit()
    return tenant, lead


async def _enqueue_inbound(db_session, tenant, lead, text: str) -> InboundMessageRow:
    await set_tenant_context(db_session, tenant.id)
    row = InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud",
        external_id=f"ext_{uuid.uuid4().hex[:8]}",
        lead_id=lead.id, from_address=lead.whatsapp_e164 or "+x",
        text=text, received_at=datetime.now(timezone.utc),
        raw={"text": {"body": text}},
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def test_pending_lead_does_not_run_step(
    db_session, session_factory
) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="pending_assignment")
    await _enqueue_inbound(db_session, tenant, lead, "first")

    adapter = FakeMessagingAdapter()
    runtime_calls = []

    async def runtime_step_stub(*args, **kwargs):
        runtime_calls.append((args, kwargs))
        raise AssertionError("step() must not be called for pending lead")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id), str(lead.id),
    )
    assert runtime_calls == []
    assert adapter.sent_messages == []


async def test_active_lead_replays_all_queued_in_order(
    db_session, session_factory
) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    await _enqueue_inbound(db_session, tenant, lead, "first")
    await _enqueue_inbound(db_session, tenant, lead, "second")
    await _enqueue_inbound(db_session, tenant, lead, "third")

    adapter = FakeMessagingAdapter()
    seen_inputs: list[str] = []

    async def runtime_step_stub(session, tenant_arg, talkflow_id, user_input):
        seen_inputs.append(user_input)
        return MagicMock(response_text=f"echo:{user_input}")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id), str(lead.id),
    )
    assert seen_inputs == ["first", "second", "third"]
    assert adapter.sent_messages == [
        ("+5511999999999", "echo:first"),
        ("+5511999999999", "echo:second"),
        ("+5511999999999", "echo:third"),
    ]


async def test_recipient_unreachable_marks_lead_and_stops(
    db_session, session_factory
) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    msg1 = await _enqueue_inbound(db_session, tenant, lead, "first")
    msg2 = await _enqueue_inbound(db_session, tenant, lead, "second")

    adapter = FakeMessagingAdapter()
    adapter.fail_next_send(RecipientUnreachable("number not on WA"))

    async def runtime_step_stub(*args, **kwargs):
        return MagicMock(response_text="hi")

    runtime = MagicMock()
    runtime.step = runtime_step_stub

    await process_lead_inbox(
        _ctx(session_factory, adapter, runtime),
        str(tenant.id), str(lead.id),
    )

    await set_tenant_context(db_session, tenant.id)
    await db_session.refresh(lead)
    assert lead.status == "unreachable"
    assert "unreachable" in (lead.unreachable_reason or "").lower()

    rows = (await db_session.execute(
        select(InboundMessageRow).where(InboundMessageRow.lead_id == lead.id)
    )).scalars().all()
    statuses = {r.id: r.status for r in rows}
    assert statuses[msg1.id] == "error"
    assert statuses[msg2.id] == "queued"  # loop stopped after first failure


async def test_concurrent_jobs_serialized_by_advisory_lock(
    db_session, session_factory
) -> None:
    tenant, lead = await _setup_tenant_with_lead(db_session, status="active")
    await _enqueue_inbound(db_session, tenant, lead, "x")

    # First job acquires the lock in a long-held session; second job should
    # see lock contention and return immediately without processing.
    import asyncio

    adapter = FakeMessagingAdapter()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_runtime_step(*args, **kwargs):
        started.set()
        await asyncio.sleep(0.5)  # hold the lock
        return MagicMock(response_text="x")

    runtime_slow = MagicMock()
    runtime_slow.step = slow_runtime_step

    runtime_fast = MagicMock()
    runtime_fast.step = MagicMock(
        side_effect=AssertionError("second job should not call step")
    )

    async def first():
        await process_lead_inbox(
            _ctx(session_factory, adapter, runtime_slow),
            str(tenant.id), str(lead.id),
        )
        finished.set()

    async def second():
        await started.wait()  # ensure first acquired the lock
        await process_lead_inbox(
            _ctx(session_factory, adapter, runtime_fast),
            str(tenant.id), str(lead.id),
        )

    await asyncio.gather(first(), second())
    assert finished.is_set()
    assert len(adapter.sent_messages) == 1
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_worker_process_lead_inbox.py -v`

Expected: FAIL — the placeholder raises NotImplementedError (or the function signature doesn't accept the ctx shape).

- [ ] **Step 3: Implement `process_lead_inbox`**

Overwrite `src/ai_sdr/worker/jobs/inbound.py`:

```python
"""process_lead_inbox — drain one lead's queued inbound messages.

Concurrency model: per-lead Postgres advisory lock. Different leads run
in parallel (different lock keys); the same lead processes its queue
serially, in `received_at ASC` order. A second job firing for the same
lead while the first is still processing returns immediately — the
first's loop will pick up new messages on its next iteration via the
in-loop re-scan.

Error taxonomy (per Plano 5 spec §8):
  - RecipientUnreachable    → mark lead.status='unreachable'; loop ends
  - WindowExpiredError      → msg.status='error', detail='window_expired';
                              Plano 9 hook (template HSM); loop ends
  - AuthError / PolicyError → msg.status='error'; log+alert; loop ends
  - MessagingError (other)  → msg.status='error'; log; loop ends
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.errors import (
    AuthError,
    MessagingError,
    PolicyError,
    RecipientUnreachable,
    WindowExpiredError,
)
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant

log = structlog.get_logger(__name__)


def _stable_lock_key(tenant_id: str, lead_id: str) -> int:
    """Compress (tenant, lead) into a signed int8 for pg_advisory_lock."""
    h = hashlib.sha256(f"{tenant_id}:{lead_id}".encode()).digest()
    # Use first 8 bytes; mask to fit in PostgreSQL's signed bigint.
    val = int.from_bytes(h[:8], "big", signed=False) & 0x7FFFFFFFFFFFFFFF
    return val


async def _fetch_next_queued(
    db: AsyncSession, lead_id: uuid.UUID
) -> InboundMessageRow | None:
    return (
        await db.execute(
            select(InboundMessageRow)
            .where(
                InboundMessageRow.lead_id == lead_id,
                InboundMessageRow.status == "queued",
            )
            .order_by(InboundMessageRow.received_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _mark_queued_as_skipped(
    db: AsyncSession, lead_id: uuid.UUID, reason: str
) -> None:
    await db.execute(
        update(InboundMessageRow)
        .where(
            InboundMessageRow.lead_id == lead_id,
            InboundMessageRow.status == "queued",
        )
        .values(status="error", error_detail=f"skipped: {reason}")
    )


async def process_lead_inbox(
    ctx: dict[str, Any], tenant_id: str, lead_id: str
) -> None:
    session_factory = ctx["session_factory"]
    registry = ctx["adapter_registry"]
    runtime = ctx.get("runtime")
    if runtime is None:
        # Production: instantiate lazily. Tests inject a stub via ctx.
        from pathlib import Path

        from ai_sdr.secrets.sops_loader import SopsLoader
        from ai_sdr.settings import get_settings
        from ai_sdr.tenant_loader.loader import TenantLoader
        from ai_sdr.treeflow.loader import TreeFlowLoader
        from ai_sdr.treeflow.runtime import TalkFlowRuntime

        tdir = Path(get_settings().tenants_dir)
        runtime = TalkFlowRuntime(
            tenant_loader=TenantLoader(tdir),
            treeflow_loader=TreeFlowLoader(tdir),
            sops_loader=SopsLoader(tdir),
        )

    tenant_uuid = uuid.UUID(tenant_id)
    lead_uuid = uuid.UUID(lead_id)
    lock_key = _stable_lock_key(tenant_id, lead_id)

    async with session_factory() as db:
        await set_tenant_context(db, tenant_uuid)

        got = (await db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
        )).scalar()
        if not got:
            log.info(
                "worker.lock_contention",
                tenant_id=tenant_id, lead_id=lead_id,
            )
            return

        try:
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == tenant_uuid))
            ).scalar_one()
            lead = (
                await db.execute(select(Lead).where(Lead.id == lead_uuid))
            ).scalar_one()

            if lead.status == "pending_assignment":
                return  # operator hasn't assigned

            if lead.status == "unreachable":
                await _mark_queued_as_skipped(db, lead.id, reason="lead_unreachable")
                await db.commit()
                return

            # status == 'active' — find the talkflow
            talkflow = (
                await db.execute(
                    select(TalkFlow).where(TalkFlow.lead_id == lead.id)
                )
            ).scalar_one_or_none()
            if talkflow is None:
                log.error(
                    "worker.active_lead_without_talkflow",
                    tenant_id=tenant_id, lead_id=lead_id,
                )
                return

            adapter = registry.get(tenant, "whatsapp_cloud")

            while True:
                msg = await _fetch_next_queued(db, lead.id)
                if msg is None:
                    break

                step_result = await runtime.step(
                    db, tenant, talkflow.id, user_input=msg.text
                )
                reply_text = step_result.response_text

                try:
                    send_result = await adapter.send_text(
                        to=msg.from_address, text=reply_text
                    )
                    msg.status = "processed"
                    msg.processed_at = datetime.now(timezone.utc)
                    log.info(
                        "worker.msg.processed",
                        msg_id=str(msg.id),
                        sent_external_id=send_result.external_id,
                    )
                except RecipientUnreachable as e:
                    lead.status = "unreachable"
                    lead.unreachable_reason = f"unreachable: {e}"
                    msg.status = "error"
                    msg.error_detail = f"unreachable: {e}"
                    log.warning(
                        "worker.recipient_unreachable",
                        lead_id=lead_id, err=str(e),
                    )
                    await db.commit()
                    return
                except WindowExpiredError as e:
                    msg.status = "error"
                    msg.error_detail = f"window_expired: {e}"
                    log.warning(
                        "worker.window_expired",
                        lead_id=lead_id, err=str(e),
                    )
                    await db.commit()
                    return
                except (AuthError, PolicyError) as e:
                    msg.status = "error"
                    msg.error_detail = f"{type(e).__name__}: {e}"
                    log.error(
                        "worker.terminal_error",
                        lead_id=lead_id, err_type=type(e).__name__, err=str(e),
                    )
                    await db.commit()
                    return
                except MessagingError as e:
                    msg.status = "error"
                    msg.error_detail = f"{type(e).__name__}: {e}"
                    log.error(
                        "worker.messaging_error",
                        lead_id=lead_id, err_type=type(e).__name__, err=str(e),
                    )
                    await db.commit()
                    return

                await db.commit()
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
            )
            await db.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_worker_process_lead_inbox.py -v`

Expected: all four tests PASS. Note: the concurrency test requires a real Postgres advisory lock to behave — make sure `make up` is running.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/inbound.py tests/integration/test_worker_process_lead_inbox.py
git commit -m "feat(plan5 t20): process_lead_inbox job — advisory lock + replay + error taxonomy"
```

---

## Task 21: `TalkFlowRuntime.create` signature update — accept `lead_id: UUID`

**Files:**
- Modify: `src/ai_sdr/treeflow/runtime.py`

**Design:** The `create()` method currently takes `lead_id: str`. After the migration to UUID FK, it must accept `lead_id: uuid.UUID`. The internal `TalkFlow(...)` constructor already wants a UUID after Task 8. This task is the type-flip — small, but it must land before Task 22 (the leads/assign endpoint) and Task 26 (simulate refactor).

- [ ] **Step 1: Change the signature**

Open `src/ai_sdr/treeflow/runtime.py`. Find the `create` method (around line 111). Change the parameter type:

```python
    async def create(
        self,
        session: AsyncSession,
        tenant: Tenant,
        lead_id: uuid.UUID,
        treeflow_id: str,
    ) -> TalkFlow:
```

`uuid` is already imported (line 5). The body already passes `lead_id=lead_id` into `TalkFlow(...)` — no body change needed.

- [ ] **Step 2: Verify no static type breakage elsewhere**

Run: `uv run mypy src/ai_sdr/`

Expected: PASS. If any caller (`simulate.py` is the most likely; the new `leads.py` route doesn't exist yet) still passes a `str`, the type-checker flags it. That's fine — we'll fix simulate in Task 26 and `leads.py` ships in Task 22.

If simulate fails the type check, leave it for Task 26 — flag as a `# type: ignore[arg-type]` temporarily, or skip `mypy` for `simulate.py` until then. Document this small debt in the commit message.

- [ ] **Step 3: Commit**

```bash
git add src/ai_sdr/treeflow/runtime.py
git commit -m "feat(plan5 t21): TalkFlowRuntime.create accepts lead_id: UUID"
```

---

## Task 22: Lead assignment — REST routes (`GET /pending` + `POST /assign`)

**Files:**
- Create: `src/ai_sdr/api/routes/leads.py`
- Modify: `src/ai_sdr/main.py` (include the router)
- Create: `tests/integration/test_leads_routes.py`

**Design:**
- `GET /tenants/{tenant_slug}/leads/pending` returns leads with `status='pending_assignment'` ordered by `created_at DESC`, with a small count of queued messages alongside.
- `POST /tenants/{tenant_slug}/leads/{lead_id}/assign` body `{treeflow_id: str}`. Steps:
  1. Validate lead exists + `status == 'pending_assignment'` (else 409).
  2. `runtime.create(db, tenant, lead.id, treeflow_id)` → talkflow.
  3. `lead.status = "active"`; commit.
  4. Enqueue `process_lead_inbox` for this lead.
  5. Return 202 with `{talkflow_id, queued_messages_to_replay}`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_leads_routes.py`:

```python
"""Lead assignment routes — pending list + assign endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.talkflow import TalkFlow
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion

pytestmark = pytest.mark.integration


@pytest.fixture
async def tenant_with_treeflow(db_session) -> tuple[Tenant, TreeflowVersion]:
    t = Tenant(slug=f"lead_{uuid.uuid4().hex[:6]}", display_name="L")
    db_session.add(t)
    await db_session.flush()
    tv = TreeflowVersion(
        tenant_id=t.id, treeflow_id="mentoria", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()
    return t, tv


async def test_pending_list_returns_only_pending(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    db_session.add_all([
        Lead(tenant_id=tenant.id, whatsapp_e164="+1", status="pending_assignment"),
        Lead(tenant_id=tenant.id, whatsapp_e164="+2", status="active"),
        Lead(tenant_id=tenant.id, whatsapp_e164="+3", status="pending_assignment"),
    ])
    await db_session.commit()

    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.get(f"/tenants/{tenant.slug}/leads/pending")
    assert r.status_code == 200
    bodies = r.json()
    assert len(bodies) == 2
    for b in bodies:
        assert b["status"] == "pending_assignment"


async def test_assign_404_on_unknown_lead(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    bogus = uuid.uuid4()
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{bogus}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 404


async def test_assign_409_when_lead_not_pending(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, _ = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+9", status="active")
    db_session.add(lead)
    await db_session.commit()

    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{lead.id}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 409


async def test_assign_happy_path_creates_talkflow_and_enqueues(
    app, db_session, tenant_with_treeflow
) -> None:
    tenant, tv = tenant_with_treeflow
    await set_tenant_context(db_session, tenant.id)
    lead = Lead(
        tenant_id=tenant.id, whatsapp_e164="+5511999999999",
        status="pending_assignment",
    )
    db_session.add(lead)
    await db_session.flush()
    # Queue two inbound messages (replay-all)
    for i in range(2):
        db_session.add(InboundMessageRow(
            tenant_id=tenant.id, provider="whatsapp_cloud",
            external_id=f"ext_{i}", lead_id=lead.id,
            from_address="+5511999999999", text=f"msg{i}",
            received_at=datetime.now(timezone.utc), raw={},
        ))
    await db_session.commit()

    enqueued: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))

    app.state.arq_pool = FakePool()

    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{lead.id}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 202
    body = r.json()
    assert "talkflow_id" in body
    assert body["queued_messages_to_replay"] == 2

    await set_tenant_context(db_session, tenant.id)
    await db_session.refresh(lead)
    assert lead.status == "active"

    tfs = (await db_session.execute(
        select(TalkFlow).where(TalkFlow.lead_id == lead.id)
    )).scalars().all()
    assert len(tfs) == 1
    assert str(tfs[0].id) == body["talkflow_id"]

    assert enqueued == [
        ("process_lead_inbox", (str(tenant.id), str(lead.id))),
    ]
```

- [ ] **Step 2: Run (expect fail)**

Run: `uv run pytest tests/integration/test_leads_routes.py -v`

Expected: FAIL with 404 for all endpoints (router not registered).

- [ ] **Step 3: Create the router**

Create `src/ai_sdr/api/routes/leads.py`:

```python
"""Lead assignment routes — pending list + assign-treeflow.

The CLI commands in `ai_sdr.cli.leads` consume these endpoints (not the
DB directly) so any future HITL UI (Plano 11) uses the same surface."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

import structlog
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_sdr.api.deps import arq_pool, db_session
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.tenant_loader.loader import TenantLoader
from ai_sdr.treeflow.loader import TreeFlowLoader
from ai_sdr.treeflow.runtime import TalkFlowRuntime
from ai_sdr.settings import get_settings

log = structlog.get_logger(__name__)
router = APIRouter()


class PendingLeadOut(BaseModel):
    id: uuid.UUID
    whatsapp_e164: str | None
    external_label: str | None
    status: str
    created_at: datetime
    queued_messages: int


class AssignBody(BaseModel):
    treeflow_id: str


class AssignOut(BaseModel):
    talkflow_id: uuid.UUID
    queued_messages_to_replay: int


async def _load_tenant(db: AsyncSession, slug: str) -> Tenant:
    t = (
        await db.execute(select(Tenant).where(Tenant.slug == slug))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail=f"tenant {slug!r} not found")
    return t


@router.get(
    "/tenants/{tenant_slug}/leads/pending",
    response_model=list[PendingLeadOut],
)
async def list_pending_leads(
    tenant_slug: str,
    db: Annotated[AsyncSession, Depends(db_session)],
) -> list[PendingLeadOut]:
    tenant = await _load_tenant(db, tenant_slug)
    await set_tenant_context(db, tenant.id)

    # Lead + count of queued inbound_messages per lead (LEFT JOIN aggregate)
    queued_count_sq = (
        select(
            InboundMessageRow.lead_id,
            func.count().label("n"),
        )
        .where(InboundMessageRow.status == "queued")
        .group_by(InboundMessageRow.lead_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Lead, queued_count_sq.c.n)
            .outerjoin(queued_count_sq, queued_count_sq.c.lead_id == Lead.id)
            .where(Lead.status == "pending_assignment")
            .order_by(Lead.created_at.desc())
        )
    ).all()
    return [
        PendingLeadOut(
            id=lead.id,
            whatsapp_e164=lead.whatsapp_e164,
            external_label=lead.external_label,
            status=lead.status,
            created_at=lead.created_at,
            queued_messages=int(n or 0),
        )
        for lead, n in rows
    ]


@router.post(
    "/tenants/{tenant_slug}/leads/{lead_id}/assign",
    response_model=AssignOut,
    status_code=202,
)
async def assign_lead(
    tenant_slug: str,
    lead_id: uuid.UUID,
    body: AssignBody,
    db: Annotated[AsyncSession, Depends(db_session)],
    pool: Annotated[ArqRedis, Depends(arq_pool)],
) -> AssignOut:
    tenant = await _load_tenant(db, tenant_slug)
    await set_tenant_context(db, tenant.id)
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail=f"lead {lead_id} not found")
    if lead.status != "pending_assignment":
        raise HTTPException(
            status_code=409,
            detail=f"lead is {lead.status}, not pending_assignment",
        )

    from pathlib import Path

    from ai_sdr.secrets.sops_loader import SopsLoader

    tdir = Path(get_settings().tenants_dir)
    runtime = TalkFlowRuntime(
        tenant_loader=TenantLoader(tdir),
        treeflow_loader=TreeFlowLoader(tdir),
        sops_loader=SopsLoader(tdir),
    )
    talkflow = await runtime.create(
        db, tenant, lead_id=lead.id, treeflow_id=body.treeflow_id
    )
    lead.status = "active"

    queued_count = (
        await db.execute(
            select(func.count(InboundMessageRow.id)).where(
                InboundMessageRow.lead_id == lead.id,
                InboundMessageRow.status == "queued",
            )
        )
    ).scalar_one()
    await db.commit()

    await pool.enqueue_job("process_lead_inbox", str(tenant.id), str(lead.id))
    log.info(
        "lead.assigned",
        tenant_slug=tenant_slug,
        lead_id=str(lead.id),
        treeflow_id=body.treeflow_id,
        queued=queued_count,
    )
    return AssignOut(
        talkflow_id=talkflow.id,
        queued_messages_to_replay=int(queued_count),
    )
```

- [ ] **Step 4: Register router in main**

Edit `src/ai_sdr/main.py`. Add the import + include:

```python
from ai_sdr.api.routes.leads import router as leads_router
# ... and inside create_app() ...
    app.include_router(leads_router)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/integration/test_leads_routes.py -v`

Expected: all four tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/api/routes/leads.py src/ai_sdr/main.py tests/integration/test_leads_routes.py
git commit -m "feat(plan5 t22): /tenants/{slug}/leads/pending + /assign REST endpoints"
```

---

## Task 23: CLI — `ai-sdr list-pending` + `ai-sdr assign-lead`

**Files:**
- Create: `src/ai_sdr/cli/leads.py`
- Modify: `src/ai_sdr/cli/app.py`

**Design:** Two typer commands that consume the REST endpoints (not the DB directly). The base URL comes from a `--api-base-url` flag (default `http://localhost:8200`). Output uses `rich.table.Table` for pretty terminal output.

- [ ] **Step 1: Create the CLI module**

Create `src/ai_sdr/cli/leads.py`:

```python
"""Lead operator CLI — list pending + assign treeflow.

Both commands hit the REST endpoints from Task 22 so the CLI and any
future UI go through one authorization/validation path.
"""

from __future__ import annotations

from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

leads_app = typer.Typer(help="Lead operator tools")
console = Console()


@leads_app.command("list-pending")
def list_pending(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    api_base_url: Annotated[
        str, typer.Option("--api-base-url")
    ] = "http://localhost:8200",
) -> None:
    """List leads with status='pending_assignment'."""
    url = f"{api_base_url}/tenants/{tenant}/leads/pending"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    items = r.json()

    if not items:
        console.print("[yellow]no pending leads[/yellow]")
        return

    table = Table(title=f"Pending leads — {tenant}")
    table.add_column("Lead ID", no_wrap=True)
    table.add_column("WhatsApp")
    table.add_column("Label")
    table.add_column("Created")
    table.add_column("Queued", justify="right")
    for it in items:
        table.add_row(
            it["id"],
            it.get("whatsapp_e164") or "-",
            it.get("external_label") or "-",
            it["created_at"],
            str(it["queued_messages"]),
        )
    console.print(table)


@leads_app.command("assign-lead")
def assign_lead(
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    lead: Annotated[str, typer.Option("--lead", help="Lead UUID")],
    treeflow: Annotated[
        str, typer.Option("--treeflow", help="TreeFlow id to attach")
    ],
    api_base_url: Annotated[
        str, typer.Option("--api-base-url")
    ] = "http://localhost:8200",
) -> None:
    """Attach a treeflow to a pending lead; worker drains queued inbounds."""
    url = f"{api_base_url}/tenants/{tenant}/leads/{lead}/assign"
    r = httpx.post(url, json={"treeflow_id": treeflow}, timeout=10.0)
    if r.status_code == 404:
        console.print(f"[red]lead not found: {lead}[/red]")
        raise typer.Exit(1)
    if r.status_code == 409:
        console.print(
            f"[red]conflict: {r.json().get('detail', 'lead not pending')}[/red]"
        )
        raise typer.Exit(1)
    r.raise_for_status()
    body = r.json()
    console.print(
        f"[green]Lead {lead} → treeflow {treeflow}. "
        f"Replaying {body['queued_messages_to_replay']} queued message(s).[/green]"
    )
    console.print(f"talkflow_id: {body['talkflow_id']}")
```

- [ ] **Step 2: Register in the top-level CLI**

Edit `src/ai_sdr/cli/app.py`:

```python
"""Top-level typer app — entrypoint registered as `ai-sdr` in pyproject."""

from __future__ import annotations

import typer

from ai_sdr.cli.leads import leads_app
from ai_sdr.cli.reindex_kb import reindex_kb_app
from ai_sdr.cli.simulate import simulate
from ai_sdr.cli.worker import worker

app = typer.Typer(help="AI SDR developer CLI")
app.command(name="simulate")(simulate)
app.command(name="worker")(worker)
app.add_typer(reindex_kb_app, name="reindex-kb")
app.add_typer(leads_app, name="leads")


if __name__ == "__main__":  # pragma: no cover
    app()
```

Note: subcommand path becomes `ai-sdr leads list-pending` and `ai-sdr leads assign-lead`. If you want the flatter `ai-sdr list-pending` form, attach directly with `app.command()` instead — but the grouped form scales better when more lead-tools land.

- [ ] **Step 3: Smoke-test help**

Run:
```bash
uv run ai-sdr leads list-pending --help
uv run ai-sdr leads assign-lead --help
```

Expected: typer prints help. No ImportError.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/cli/leads.py src/ai_sdr/cli/app.py
git commit -m "feat(plan5 t23): ai-sdr leads list-pending + assign-lead CLI"
```

---

## Task 24: Adapter compliance test suite — parametrized contract

**Files:**
- Create: `tests/integration/test_adapter_compliance.py`

**Design:** Parametrized test class that runs identical contract assertions against multiple adapter implementations. Today: `["fake", "whatsapp_cloud_mocked"]`. When `VialumChatAdapter` lands, add it to the param list — no other test changes. This is the proof that the adapter pattern delivers what the ADR promised: drop-in compatibility.

- [ ] **Step 1: Create the suite**

Create `tests/integration/test_adapter_compliance.py`:

```python
"""Adapter-compliance suite — runs identical contract tests against every
MessagingAdapter impl. To add a new impl, append its key to the
`@pytest.fixture(params=[...])` below and provide a builder.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from ai_sdr.messaging.base import InboundMessage, MessagingAdapter, SendResult
from ai_sdr.messaging.errors import (
    AuthError,
    RecipientUnreachable,
    SignatureError,
    TransientError,
)
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"

pytestmark = pytest.mark.integration


def _build_whatsapp_mocked(monkeypatch) -> tuple[MessagingAdapter, dict]:
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999", "wa_token": "EAA",
        "wa_verify": "vt", "wa_app_secret": "appsecret",
    }
    adapter = WhatsAppCloudAPIAdapter(cfg, secrets)
    import tenacity
    monkeypatch.setattr(
        "ai_sdr.messaging.whatsapp_cloud._WAIT_STRATEGY", tenacity.wait_none()
    )
    helpers = {
        "app_secret": "appsecret",
        "build_inbound_body": lambda: (FIXTURES / "inbound_text.json").read_bytes(),
        "expected_external_id": "wamid.HBgM_FIRSTMESSAGE_AAAA=",
        "expected_from_address": "+5511988887777",
    }
    return adapter, helpers


def _build_fake() -> tuple[MessagingAdapter, dict]:
    adapter = FakeMessagingAdapter()
    msg = InboundMessage(
        external_id="fake_ext_1",
        from_address="+5511988887777",
        text="oi",
        received_at_iso="2026-05-25T12:00:00+00:00",
        raw={"id": "fake_ext_1"},
    )
    adapter.queue_inbound(msg)
    helpers = {
        "app_secret": None,
        "build_inbound_body": lambda: b"",
        "expected_external_id": "fake_ext_1",
        "expected_from_address": "+5511988887777",
    }
    return adapter, helpers


@pytest.fixture(params=["fake", "whatsapp_cloud_mocked"])
def adapter_under_test(request, monkeypatch) -> tuple[MessagingAdapter, dict]:
    if request.param == "fake":
        return _build_fake()
    return _build_whatsapp_mocked(monkeypatch)


def _sign(body: bytes, secret: str | None) -> dict:
    if secret is None:
        return {}
    return {
        "x-hub-signature-256": "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
    }


async def test_handle_inbound_returns_normalized_message(adapter_under_test) -> None:
    adapter, helpers = adapter_under_test
    body = helpers["build_inbound_body"]()
    headers = _sign(body, helpers["app_secret"])
    msgs = await adapter.handle_inbound(body, headers)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.external_id == helpers["expected_external_id"]
    assert m.from_address == helpers["expected_from_address"]
    assert m.text != ""


async def test_handle_inbound_raises_signature_error_on_tampered_payload(
    adapter_under_test,
) -> None:
    adapter, helpers = adapter_under_test
    if helpers["app_secret"] is None:
        pytest.skip("fake adapter does not enforce HMAC")
    body = helpers["build_inbound_body"]()
    with pytest.raises(SignatureError):
        await adapter.handle_inbound(
            body, headers={"x-hub-signature-256": "sha256=" + "0" * 64}
        )


async def test_send_text_returns_external_id(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, WhatsAppCloudAPIAdapter):
        monkeypatch.setattr(
            "ai_sdr.messaging.whatsapp_cloud._build_http_client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(
                        200, json={"messages": [{"id": "wamid.OUT_X="}]}
                    )
                ),
                timeout=15.0,
            ),
        )
    r = await adapter.send_text("+5511988887777", "hi")
    assert isinstance(r, SendResult)
    assert r.external_id


async def test_send_text_raises_recipient_unreachable(
    adapter_under_test, monkeypatch
) -> None:
    adapter, helpers = adapter_under_test
    if isinstance(adapter, FakeMessagingAdapter):
        adapter.fail_next_send(RecipientUnreachable("number not on WA"))
    else:
        # WhatsApp mocked: 400/131026
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
        await adapter.send_text("+5511988887777", "hi")


def test_verification_challenge_signature(adapter_under_test) -> None:
    """All adapters must expose verification_challenge — either echo or None."""
    adapter, _ = adapter_under_test
    out = adapter.verification_challenge({})
    assert out is None or isinstance(out, str)
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/integration/test_adapter_compliance.py -v`

Expected: all tests PASS for both `fake` and `whatsapp_cloud_mocked` params. Skipped tests (for fake when not applicable) report as `s`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_adapter_compliance.py
git commit -m "test(plan5 t24): adapter-compliance suite (fake + whatsapp_cloud_mocked)"
```

---

## Task 25: End-to-end integration test — webhook → worker → reply

**Files:**
- Create: `tests/integration/test_messaging_e2e.py`

**Design:** A single test that drives the full happy path. Posts a signed WhatsApp webhook body, waits for the inbound row, manually invokes `process_lead_inbox` (simulating the worker), then asserts `FakeMessagingAdapter` recorded the reply. This is the integration-level proof that all layers from Task 1–24 compose correctly.

Bootstrap: lead arrives `pending_assignment`, no reply yet. Then assign via the REST endpoint → talkflow created. Re-invoke worker → reply is delivered.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_messaging_e2e.py`:

```python
"""End-to-end: webhook → ingest → assign → worker drains queue → adapter.send."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_sdr.db.engine import build_engine
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.models.treeflow_version import TreeflowVersion
from ai_sdr.settings import get_settings
from ai_sdr.worker.jobs.inbound import process_lead_inbox

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "whatsapp"


async def test_end_to_end_webhook_assign_worker_reply(
    app, db_session
) -> None:
    # --- 1. Set up tenant + treeflow + a registry that returns a FakeAdapter ---
    tenant = Tenant(slug=f"e2e_{uuid.uuid4().hex[:6]}", display_name="E2E")
    db_session.add(tenant)
    await db_session.flush()
    tv = TreeflowVersion(
        tenant_id=tenant.id, treeflow_id="mentoria", version="1.0.0",
        content_hash="x" * 64,
        content_yaml="id: mentoria\nversion: 1.0.0\nentry_node: n1\nnodes: {n1: {prompt: hi}}\n",
    )
    db_session.add(tv)
    await db_session.commit()

    # Static registry: pretend tenant.yaml says provider=whatsapp_cloud, but we
    # mount a real WhatsAppCloudAPIAdapter with known secrets so we can sign
    # the inbound webhook body. The *outbound* send_text path is patched to
    # a FakeMessagingAdapter so we don't hit the real Graph API.
    from ai_sdr.messaging.whatsapp_cloud import WhatsAppCloudAPIAdapter
    from ai_sdr.schemas.tenant_yaml import MessagingConfig
    cfg = MessagingConfig(
        provider="whatsapp_cloud",
        phone_number_id_ref="secrets/wa_phone_id",
        access_token_ref="secrets/wa_token",
        webhook_verify_token_ref="secrets/wa_verify",
        app_secret_ref="secrets/wa_app_secret",
    )
    secrets = {
        "wa_phone_id": "999", "wa_token": "EAA",
        "wa_verify": "vt", "wa_app_secret": "appsecret_e2e",
    }
    wa_adapter = WhatsAppCloudAPIAdapter(cfg, secrets)

    fake_for_send = FakeMessagingAdapter()

    class HybridAdapter:
        """For inbound, behave like WhatsApp (HMAC + parser). For send_text,
        delegate to the FakeMessagingAdapter so we don't network out."""
        async def handle_inbound(self, body, headers):
            return await wa_adapter.handle_inbound(body, headers)
        async def send_text(self, to, text):
            return await fake_for_send.send_text(to, text)
        def verification_challenge(self, params):
            return wa_adapter.verification_challenge(params)

    hybrid = HybridAdapter()

    class StaticRegistry:
        def get(self, tenant, provider): return hybrid

    app.state.adapter_registry = StaticRegistry()

    # arq pool that simply records jobs (we'll invoke them manually below).
    enqueued: list[tuple] = []
    class FakePool:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued.append((name, args))
    app.state.arq_pool = FakePool()

    # --- 2. POST a signed inbound webhook ---
    body = (FIXTURES / "inbound_text.json").read_bytes()
    sig = "sha256=" + hmac.new(b"appsecret_e2e", body, hashlib.sha256).hexdigest()
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post(
            f"/webhooks/{tenant.slug}/whatsapp_cloud",
            content=body, headers={"x-hub-signature-256": sig},
        )
    assert r.status_code == 200

    # --- 3. The lead is now pending; the worker job should no-op on it ---
    await set_tenant_context(db_session, tenant.id)
    lead = (await db_session.execute(select(Lead))).scalar_one()
    assert lead.status == "pending_assignment"

    engine = build_engine(get_settings().database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = MagicMock(); registry.get.return_value = hybrid
    runtime = MagicMock()
    runtime.step = MagicMock(side_effect=AssertionError("must not step on pending lead"))

    await process_lead_inbox(
        {"session_factory": session_factory, "adapter_registry": registry, "runtime": runtime},
        str(tenant.id), str(lead.id),
    )
    assert fake_for_send.sent_messages == []

    # --- 4. Operator assigns ---
    async with AsyncClient(app=app, base_url="http://test") as client:
        r = await client.post(
            f"/tenants/{tenant.slug}/leads/{lead.id}/assign",
            json={"treeflow_id": "mentoria"},
        )
    assert r.status_code == 202

    # --- 5. Worker runs again (now lead is active) and replies ---
    async def runtime_step_stub(session, t, talkflow_id, user_input):
        return MagicMock(response_text="Olá! Recebi sua mensagem.")
    runtime_alive = MagicMock()
    runtime_alive.step = runtime_step_stub

    await process_lead_inbox(
        {
            "session_factory": session_factory,
            "adapter_registry": registry,
            "runtime": runtime_alive,
        },
        str(tenant.id), str(lead.id),
    )
    assert fake_for_send.sent_messages == [
        ("+5511988887777", "Olá! Recebi sua mensagem."),
    ]

    # All inbounds processed
    rows = (await db_session.execute(select(InboundMessageRow))).scalars().all()
    assert {r.status for r in rows} == {"processed"}
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/integration/test_messaging_e2e.py -v`

Expected: PASS. Postgres + Redis must be up (`make up`).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_messaging_e2e.py
git commit -m "test(plan5 t25): end-to-end webhook → assign → worker → reply"
```

---

## Task 26: Refactor `simulate` to use the new Lead model + `external_label`

**Files:**
- Modify: `src/ai_sdr/cli/simulate.py`

**Design:** Today simulate passes a free-form string as `lead_id`. After Task 8 changed `TalkFlow.lead_id` to UUID, simulate must:
1. Find-or-create a Lead by `(tenant_id, external_label=<--lead value>)`.
2. Set the new Lead's `status='active'` (NOT `pending_assignment` — simulate is a dev tool, must not require HITL flow).
3. Pass `lead.id` (UUID) to `runtime.create()`.

The `/restart` REPL command should also remove the Lead's TalkFlow (existing behavior) — it can leave the Lead row in place (or delete; either is fine for dev).

- [ ] **Step 1: Read the current simulate.py to understand the existing flow**

Run: `cat src/ai_sdr/cli/simulate.py`

Look for the lines that:
- Pass `lead_id` into `runtime.create`
- Handle `/restart` command (deletes the TalkFlow row)

- [ ] **Step 2: Update simulate.py**

Inside `simulate.py`, locate the `_run` async function. Before the `runtime.create(...)` call (which currently takes the string `lead` directly), add a find-or-create block. Replace the section where `runtime.create` is called with this pattern:

```python
from ai_sdr.db.rls import set_tenant_context
from ai_sdr.models.lead import Lead

# ... existing code ...

# Find-or-create a dev Lead by external_label so the foreign-key in
# talkflows.lead_id can be satisfied. Simulate marks the lead 'active'
# so the worker (if running) won't lock waiting on assignment.
async with session_maker() as session:
    tenant_row = (
        await session.execute(select(Tenant).where(Tenant.slug == tenant))
    ).scalar_one()
    await set_tenant_context(session, tenant_row.id)
    dev_lead = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_row.id,
                Lead.external_label == lead,
            )
        )
    ).scalar_one_or_none()
    if dev_lead is None:
        dev_lead = Lead(
            tenant_id=tenant_row.id,
            external_label=lead,
            status="active",
        )
        session.add(dev_lead)
        await session.commit()

# Then below, pass the UUID:
# talkflow = await runtime.create(session, tenant_row, lead_id=dev_lead.id, treeflow_id=treeflow)
```

Adapt the variable names to match what currently exists in `simulate.py` (the script may use different names for the session maker / tenant lookup). The point is: insert a find-or-create-by-`external_label` and pass `dev_lead.id` to `runtime.create`.

For `/restart`: today it deletes the TalkFlow row. Keep that. Also remove the Lead's reference (or just delete the TalkFlow — the Lead can stay). If you delete TalkFlow first, the Lead is left orphaned but harmless.

- [ ] **Step 3: Smoke-test simulate**

Pre-conditions: `make up` is running; the example tenant + a treeflow are seeded (existing make targets handle this). The OPENAI_API_KEY / ANTHROPIC_API_KEY are set.

Run:
```bash
uv run ai-sdr simulate --tenant example --treeflow example --lead test-1
```

Expected:
- The CLI prompts you for the first user input.
- On Enter (empty input), the agent greets you (whatever the example treeflow's entry node prompt produces).
- `/quit` exits.
- Re-running the same command resumes the conversation (existing checkpointer behavior — `(tenant_id, external_label='test-1')` resolves to the same Lead → same TalkFlow).

If you check the DB after running, `leads` has one row with `external_label='test-1'` and `status='active'`.

- [ ] **Step 4: Commit**

```bash
git add src/ai_sdr/cli/simulate.py
git commit -m "feat(plan5 t26): simulate find-or-creates Lead by external_label"
```

---

## Task 27: Wiring — docker-compose worker + example tenant.yaml + CLAUDE.md

**Files:**
- Modify: `docker-compose.yml`
- Modify: `tenants/example/tenant.yaml`
- Modify: `tenants/example/secrets.enc.yaml` (only the unencrypted *.yaml fixture; the encrypted production version is updated on the VPS by the operator)
- Modify: `CLAUDE.md`

**Design:** Ship the deployable wiring + documentation. The example tenant.yaml gets a `messaging:` block pointing at `provider: fake` (so dev runs without WhatsApp creds); production tenants override to `whatsapp_cloud`. Docker compose gains a `worker` service. CLAUDE.md gains a "Messaging (Plano 5)" section.

- [ ] **Step 1: Update docker-compose.yml**

Open `docker-compose.yml` and add a new service mirroring the API but running the worker command. Insert after the existing `api` service:

```yaml
  worker:
    build: .
    command: uv run ai-sdr worker
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://ai_sdr_app:ai_sdr_app@postgres:5432/ai_sdr
      REDIS_URL: redis://redis:6379/0
      APP_ENV: ${APP_ENV:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      TENANTS_DIR: /app/tenants
      SOPS_AGE_KEY_FILE: /app/.sops/age.key
    volumes:
      - ./tenants:/app/tenants:ro
      - ./.sops:/app/.sops:ro
    restart: unless-stopped
```

Adjust env vars / volume mounts to match what the existing `api` service uses — they should be identical apart from `command`.

- [ ] **Step 2: Update `tenants/example/tenant.yaml`**

Open `tenants/example/tenant.yaml`. Add a `messaging:` block at the top level. Default to `fake` so dev works without WhatsApp credentials:

```yaml
messaging:
  provider: fake
  # When switching this tenant to live WhatsApp, change provider to
  # 'whatsapp_cloud' and uncomment + populate the *_ref fields. The
  # referenced secret names must exist in tenants/example/secrets.enc.yaml.
  #
  # provider: whatsapp_cloud
  # phone_number_id_ref: secrets/wa_phone_id
  # access_token_ref: secrets/wa_token
  # webhook_verify_token_ref: secrets/wa_verify
  # app_secret_ref: secrets/wa_app_secret
  # api_version: v21.0
```

- [ ] **Step 3: Document the WhatsApp secret keys**

Add a comment block at the top of `tenants/example/secrets.enc.yaml` (decrypt with `sops`, edit, re-encrypt). For dev, the file may be unencrypted in the repo — add the keys as placeholders so a future tenant copy-paste has the right shape:

```yaml
# WhatsApp Cloud API secrets — populate ONLY when messaging.provider=='whatsapp_cloud'
# in tenant.yaml. These references must match the *_ref fields there.
wa_phone_id: ""        # numeric phone_number_id from WhatsApp Business Manager
wa_token: ""           # long-lived system user access token
wa_verify: ""          # operator-chosen verify token (matches Meta webhook config)
wa_app_secret: ""      # the Meta App's App Secret (signs X-Hub-Signature-256)
```

(If the file is SOPS-encrypted, do the edit through `sops tenants/example/secrets.enc.yaml`.)

- [ ] **Step 4: Update CLAUDE.md**

Open `CLAUDE.md`. Add a new section after the existing "Guardrails (Plan 3)" section:

````markdown
## Messaging (Plano 5)

- Adapter contract: `src/ai_sdr/messaging/base.py` (`MessagingAdapter` ABC + `InboundMessage`/`SendResult` dataclasses).
- Default standalone impl: `whatsapp_cloud` (`whatsapp_cloud.py`). Fake impl for dev/tests: `fake.py`.
- Choose impl via `tenant.yaml > messaging.provider`. For `whatsapp_cloud`, set the four `*_ref` fields (all under the `secrets/` prefix convention).
- Webhook URLs: `https://<host>/webhooks/<tenant_slug>/<provider>`. GET = handshake (WhatsApp `hub.mode=subscribe`); POST = ingestion.
- Idempotency: dedupe via UNIQUE `(tenant_id, provider, external_id)` on `inbound_messages`. Repeated webhooks = no-op insert.
- Worker (`uv run ai-sdr worker`, or the `worker` docker-compose service in prod): consumes `process_lead_inbox` jobs from the Redis queue. Serialization per-lead via `pg_advisory_lock`. **Always run the worker in production** — the API does not process inbounds.
- Bootstrap (HITL-friendly): a brand-new lead nasce `status='pending_assignment'`. Mensagens ficam queued no DB; **nada acontece** até operador atribuir treeflow via:
  - `ai-sdr leads list-pending --tenant <slug>` (lista)
  - `ai-sdr leads assign-lead --tenant <slug> --lead <uuid> --treeflow <id>` (atribui)
  - `POST /tenants/<slug>/leads/<uuid>/assign {treeflow_id}` (REST)
- Replay-all: ao atribuir, o worker processa todas as inbounds acumuladas em `received_at ASC`.
- Erros tipados (`messaging/errors.py`):
  - `RecipientUnreachable` → marca `lead.status='unreachable'`; worker para.
  - `WindowExpiredError` → marca msg como `error`; **hook do Plano 9** (template HSM).
  - `AuthError`, `PolicyError` → log + alert; worker para (sem retry — precisa de operador).
  - `TransientError` / `RateLimitError` (429) → adapter resolve internamente via `tenacity` (3 tentativas, backoff exponencial, respeita `Retry-After`).
- Adapter compliance: `tests/integration/test_adapter_compliance.py` é parametrizado por impl — qualquer novo adapter (Vialum Chat etc.) entra apenas adicionando ao `params`.

### Adding a new tenant's WhatsApp config

1. No painel Meta Business Manager: obtenha `phone_number_id`, gere um system-user access token de longa duração, configure o webhook URL (`/webhooks/<slug>/whatsapp_cloud`) com um `verify_token` que você escolhe, e copie o **App Secret** da Meta App.
2. Em `tenants/<slug>/secrets.enc.yaml` (via SOPS): salve `wa_phone_id`, `wa_token`, `wa_verify`, `wa_app_secret`.
3. Em `tenants/<slug>/tenant.yaml`: defina o bloco `messaging:` apontando pra `whatsapp_cloud` com as 4 *_ref.
4. Restart da API (re-carrega `tenant.yaml`) e do worker.

### Simulator vs worker

- `ai-sdr simulate` continua sendo dev tool — NÃO usa adapter de WhatsApp. Cria/reusa um Lead por `external_label`, marca como `status='active'` automaticamente.
- Em produção: NUNCA rode `simulate` apontando pra tenant real; use `worker` + webhook.

````

- [ ] **Step 5: Self-check the worker boots locally**

Run (in two terminals):

Terminal 1: `make up && docker compose up -d worker` (or `uv run ai-sdr worker` directly if not using compose).

Terminal 2: tail logs to see `worker.ready`.

Expected: structlog emits `worker.starting` → `checkpointer.ready` → `worker.ready` and then sits idle waiting for jobs.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml tenants/example/tenant.yaml tenants/example/secrets.enc.yaml CLAUDE.md
git commit -m "feat(plan5 t27): docker-compose worker + example tenant messaging config + CLAUDE.md"
```

---

## Final task: Plano 5 close-out

- [ ] **Step 1: Run the full test suite**

Run:
```bash
make lint && make format && make type && make test-unit
uv run pytest tests/integration/ -v
```

Expected: all PASS. No new lint/type warnings introduced by Plano 5 files.

- [ ] **Step 2: Smoke the live path (manual)**

If WhatsApp credentials are available, configure `tenants/example/tenant.yaml` per CLAUDE.md, restart the worker, register the webhook URL in Meta, and send a message from a real WhatsApp number to the business phone. Verify:

1. The webhook hits the API, returns 200, and `inbound_messages` has the row.
2. The lead is in `pending_assignment`.
3. `ai-sdr leads list-pending --tenant example` shows the lead.
4. `ai-sdr leads assign-lead --tenant example --lead <uuid> --treeflow example` assigns.
5. The lead receives the agent's first reply on WhatsApp within ~15s.

If credentials are NOT available, skip — the e2e integration test in Task 25 covers the equivalent path with the FakeMessagingAdapter on the send side.

- [ ] **Step 3: Tag the close-out commit**

```bash
git commit --allow-empty -m "chore(plan5): close-out — all tasks landed, suite green"
```

---

## Notes for plan execution

- **Migration ordering matters.** Tasks 3, 5, 7 introduce 0006, 0007, 0008 respectively. Each task ends with `alembic upgrade head` — don't skip the upgrade between tasks.
- **The worker test (Task 20) holds a real Postgres advisory lock.** If you abort the test process mid-run, run `docker exec ai_sdr_postgres psql -U ai_sdr_app -d ai_sdr -c "SELECT pg_advisory_unlock_all();"` to free leaked locks.
- **`TalkFlowRuntime.create`'s signature change (Task 21) is type-only.** Existing tests from Plano 2/3 that passed a string `lead_id` to the runtime need an `external_label` Lead created first. Fix them as you go (search: `runtime.create.*lead_id=`).
- **arq + structlog interaction.** arq has its own logger config; structlog's processors should still emit JSON, but check `worker.*` logs land in the same format as `app.*` logs in dev.
