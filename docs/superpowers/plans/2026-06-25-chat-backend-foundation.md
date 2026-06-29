# Chat Backend — Data & Read Foundation (Plano 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-side backend that feeds the contact-based operator inbox: an `instances` table, a per-contact read-state table, and the authenticated JSON read API (instances → contacts → messages) — all RLS-scoped.

**Architecture:** New tables `instances` and `operator_read_markers` (additive migrations, RLS mirroring `talks`). A new router `api/routes/console_inbox.py` behind the existing cookie-auth dep `require_tenant_access` (which resolves tenant + sets RLS). The contact list is **anchored on `leads`** (so contacts without a Talk render); last-message/preview/unread are **computed at query time** via subqueries (denormalization onto `leads` is deferred to Plano 2, where the worker writes it). Talk is read as an overlay (active-talk badge + funnel).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, Pydantic v2, pytest (`pytest.mark.integration`), uv. Postgres with RLS.

## Global Constraints

- **RLS:** every read route uses `require_tenant_access` (from `ai_sdr.web.auth`) which sets `app.current_tenant` and returns `(Tenant, User)`. Never query tenant-scoped tables without it. New tables get `ENABLE/FORCE ROW LEVEL SECURITY` + a policy `USING (tenant_id = current_setting('app.current_tenant', true)::uuid)` — mirror `migrations/versions/0028_action_executions.py`.
- **Migrations chain linearly.** Current head is `0031_add_voice_synthesis_failed_reason`. New migrations are `0032`, `0033` in order. Each has `revision`, `down_revision`, `upgrade()`, `downgrade()`.
- **Contact-based, Lead-anchored:** the contact list is driven by `leads` (a lead with NO Talk must appear). Talk data is a LEFT-JOIN overlay.
- **Instance = (tenant + channel_label).** `Lead.inbound_channel_label` (default `'main'`) is the channel key. v1 backfills one `'main'` instance per tenant.
- **Auth/RLS test scaffolding:** route tests must authenticate. **Mirror `tests/integration/test_console_leads_page.py`** for the exact pattern (seed a `User` + `UserTenantAccess`, sign the `pesdr_session` cookie via `ai_sdr.web.auth.sign_session_cookie`, and use a tenant whose on-disk config has `console.enabled=true` OR monkeypatch `tenant_loader_dep`). Do NOT reinvent it.
- **Integration tests** need the test Postgres (env: `.env` + tunnel, DB at head). Run `uv run pytest tests/integration/<file> -q`. Run integration separately from unit (conftest clobber).
- **TDD:** failing test → confirm fail → minimal impl → confirm pass → commit. Commit messages: `feat(chat-be): …`.

---

### Task 1: `instances` table + model + migration (0032) + backfill

**Files:**
- Create: `src/ai_sdr/models/instance.py`
- Modify: `src/ai_sdr/models/__init__.py` (register the import)
- Create: `migrations/versions/0032_instances.py`
- Test: `tests/integration/test_instances_model.py`

**Interfaces:**
- Produces: `Instance` ORM (`id, tenant_id, channel_label, phone_e164, display_name, created_at`), `UNIQUE(tenant_id, channel_label)`, RLS by `tenant_id`. Migration backfills one row per existing tenant: `(tenant_id, channel_label='main', phone_e164=NULL, display_name=tenant.display_name)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_instances_model.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from ai_sdr.models.instance import Instance
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def test_instance_insert_and_rls_scoping(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)}
    )
    inst = Instance(tenant_id=tenant.id, channel_label="main", display_name="T")
    db_session.add(inst)
    await db_session.flush()

    rows = (await db_session.execute(select(Instance).where(Instance.tenant_id == tenant.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel_label == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_instances_model.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.models.instance`.

- [ ] **Step 3: Write the model**

```python
# src/ai_sdr/models/instance.py
"""Instance — a (tenant + channel) operating line. The inbox scope + WS channel key.

An instance = one messaging channel of a tenant (today one WhatsApp number,
keyed by Lead.inbound_channel_label). Funnel is NOT part of the instance —
it's an orthogonal filter on Talk.treeflow_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class Instance(Base):
    __tablename__ = "instances"
    __table_args__ = (UniqueConstraint("tenant_id", "channel_label", name="uq_instances_tenant_channel"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel_label: Mapped[str] = mapped_column(Text(), nullable=False, server_default="main")
    phone_e164: Mapped[str | None] = mapped_column(Text(), nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

Add the missing import at the top: `from sqlalchemy import DateTime` (merge into the existing sqlalchemy import line). Then register in `src/ai_sdr/models/__init__.py`:

```python
from ai_sdr.models.instance import Instance  # noqa: F401
```

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/0032_instances.py
"""instances table + RLS + backfill one 'main' instance per tenant.

Revision ID: 0032_instances
Revises: 0031_add_voice_synthesis_failed_reason
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_instances"
down_revision = "0031_add_voice_synthesis_failed_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_label", sa.Text(), nullable=False, server_default="main"),
        sa.Column("phone_e164", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "channel_label", name="uq_instances_tenant_channel"),
    )
    op.execute("ALTER TABLE instances ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE instances FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY instances_tenant_isolation ON instances "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )
    # Backfill: one 'main' instance per existing tenant.
    op.execute(
        "INSERT INTO instances (tenant_id, channel_label, display_name) "
        "SELECT id, 'main', display_name FROM tenants "
        "ON CONFLICT (tenant_id, channel_label) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS instances_tenant_isolation ON instances")
    op.drop_table("instances")
```

- [ ] **Step 5: Apply migration + run test**

Run: `uv run alembic upgrade head` (expect `0032_instances`), then `uv run pytest tests/integration/test_instances_model.py -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/instance.py src/ai_sdr/models/__init__.py migrations/versions/0032_instances.py tests/integration/test_instances_model.py
git commit -m "feat(chat-be): instances table + model + per-tenant backfill"
```

---

### Task 2: `operator_read_markers` table + model + migration (0033)

**Files:**
- Create: `src/ai_sdr/models/operator_read_marker.py`
- Modify: `src/ai_sdr/models/__init__.py`
- Create: `migrations/versions/0033_operator_read_markers.py`
- Test: `tests/integration/test_read_markers_model.py`

**Interfaces:**
- Produces: `OperatorReadMarker` (`tenant_id, user_id, lead_id, last_read_at, last_read_message_at`) PK `(user_id, lead_id)`, RLS by `tenant_id`. Read-state is **per contact (lead)**, not per Talk. `last_read_message_at` (timestamp) is the high-water mark used to compute unread = messages with time > marker.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_read_markers_model.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ai_sdr.models.operator_read_marker import OperatorReadMarker
from ai_sdr.models.tenant import Tenant

pytestmark = pytest.mark.integration


async def test_read_marker_upsert(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)}
    )
    uid, lid = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    stmt = pg_insert(OperatorReadMarker).values(
        tenant_id=tenant.id, user_id=uid, lead_id=lid, last_read_at=now, last_read_message_at=now
    ).on_conflict_do_update(
        index_elements=["user_id", "lead_id"],
        set_={"last_read_at": now, "last_read_message_at": now},
    )
    await db_session.execute(stmt)
    await db_session.execute(stmt)  # idempotent upsert
    row = await db_session.get(OperatorReadMarker, {"user_id": uid, "lead_id": lid})
    assert row is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_read_markers_model.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.models.operator_read_marker`.

- [ ] **Step 3: Write the model**

```python
# src/ai_sdr/models/operator_read_marker.py
"""OperatorReadMarker — per-(operator, contact) read high-water mark.

Read-state is per CONTACT (lead), not per Talk: the contact-based inbox
shows one unread count per contact. unread = messages newer than
last_read_message_at.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_sdr.db.base import Base


class OperatorReadMarker(Base):
    __tablename__ = "operator_read_markers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_read_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Register in `models/__init__.py`: `from ai_sdr.models.operator_read_marker import OperatorReadMarker  # noqa: F401`.

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/0033_operator_read_markers.py
"""operator_read_markers — per-(operator, contact) read state + RLS.

Revision ID: 0033_operator_read_markers
Revises: 0032_instances
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_operator_read_markers"
down_revision = "0032_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_read_markers",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_read_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_read_message_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "lead_id", name="pk_operator_read_markers"),
    )
    op.create_index("ix_read_markers_lead", "operator_read_markers", ["lead_id"])
    op.execute("ALTER TABLE operator_read_markers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operator_read_markers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY read_markers_tenant_isolation ON operator_read_markers "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS read_markers_tenant_isolation ON operator_read_markers")
    op.drop_table("operator_read_markers")
```

- [ ] **Step 5: Apply + run test**

Run: `uv run alembic upgrade head` (expect `0033_operator_read_markers`), then `uv run pytest tests/integration/test_read_markers_model.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_sdr/models/operator_read_marker.py src/ai_sdr/models/__init__.py migrations/versions/0033_operator_read_markers.py tests/integration/test_read_markers_model.py
git commit -m "feat(chat-be): operator_read_markers (per-contact read state)"
```

---

### Task 3: Pydantic response schemas for the inbox API

**Files:**
- Create: `src/ai_sdr/api/schemas/console_inbox.py`
- Test: `tests/unit/test_console_inbox_schemas.py`

**Interfaces:**
- Produces: `InstanceOut`, `ContactOut`, `ContactDetailOut`, `MessageOut`, `ReadBody` Pydantic models with the exact fields the routes (Tasks 4-8) return.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_console_inbox_schemas.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ai_sdr.api.schemas.console_inbox import ContactOut, InstanceOut, MessageOut


def test_contact_out_serializes_state_and_funnel():
    c = ContactOut(
        lead_id=uuid.uuid4(), display_name="João", whatsapp_e164="+5511",
        last_message_at=datetime.now(timezone.utc), last_message_preview="oi",
        state="ai", funnel_node="proposta", unread=2,
    )
    assert c.state == "ai"
    assert c.unread == 2


def test_message_out_side_and_kind():
    m = MessageOut(
        id=uuid.uuid4(), direction="out", origin="operator",
        text="oi", media_type="text", at=datetime.now(timezone.utc),
    )
    assert m.direction == "out"
    assert m.origin == "operator"


def test_instance_out():
    i = InstanceOut(id=uuid.uuid4(), channel_label="main", display_name="Avelum", phone_e164=None)
    assert i.channel_label == "main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_console_inbox_schemas.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the schemas**

```python
# src/ai_sdr/api/schemas/console_inbox.py
"""Response schemas for the contact-based operator inbox API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ContactState = Literal["ai", "requires_review", "human", "awaiting", "closed"]


class InstanceOut(BaseModel):
    id: uuid.UUID
    channel_label: str
    display_name: str | None
    phone_e164: str | None


class ContactOut(BaseModel):
    lead_id: uuid.UUID
    display_name: str | None
    whatsapp_e164: str | None
    last_message_at: datetime | None
    last_message_preview: str | None
    state: ContactState
    funnel_node: str | None
    unread: int


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: Literal["in", "out"]
    origin: Literal["lead", "ai", "operator"]
    text: str | None
    media_type: str
    audio_url: str | None = None
    transcription: str | None = None
    at: datetime


class ContactDetailOut(BaseModel):
    lead_id: uuid.UUID
    display_name: str | None
    whatsapp_e164: str | None
    state: ContactState
    funnel_node: str | None
    active_talk_id: uuid.UUID | None
    ai_reasoning: str | None
    window_open: bool
    window_expires_at: datetime | None


class ReadBody(BaseModel):
    last_read_message_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_console_inbox_schemas.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/schemas/console_inbox.py tests/unit/test_console_inbox_schemas.py
git commit -m "feat(chat-be): inbox API response schemas"
```

---

### Task 4: Inbox read repository — contact list + messages + state derivation

**Files:**
- Create: `src/ai_sdr/repositories/inbox_repository.py`
- Test: `tests/integration/test_inbox_repository.py`

**Interfaces:**
- Consumes: `Instance` (T1), `OperatorReadMarker` (T2), `Lead`, `Talk`, `InboundMessageRow`, `OutboundMessageRow`.
- Produces:
  - `async list_contacts(session, *, tenant_id, channel_label, user_id, status=None, funnel=None, q=None, limit=50, before=None) -> list[ContactRow]` where `ContactRow` is a dataclass carrying lead fields + computed `last_message_at`, `last_message_preview`, `active_talk` (Talk|None), `funnel_node`, `unread`.
  - `async list_messages(session, *, lead_id, before=None, limit=50) -> list[MessageRow]` — merged inbound+outbound for the lead, ordered by time DESC, cursor by time.
  - `def derive_state(active_talk) -> ContactState` — pure: maps (talk.status, talk.handling_mode) → `ai|requires_review|human|awaiting|closed` (no active talk → `awaiting` if lead has no closed talks else `closed`).

> Implementation guidance: the contact list is **Lead-anchored**. Base query: `select Lead where Lead.inbound_channel_label == channel_label` (RLS already scopes tenant). LEFT JOIN the lead's **active** Talk (`Talk.status IN ('active','requires_review')`, one per lead). Compute `last_message_at` = `GREATEST(max(inbound.received_at), max(outbound.sent_at))` per lead via correlated subqueries (mirror the queued-count subquery pattern in `api/routes/leads.py:72`). `unread` = count of inbound messages with `received_at > marker.last_read_message_at` (LEFT JOIN `operator_read_markers` on `(user_id, lead_id)`; null marker → all inbound count). `funnel_node` = the active talk's current node — read from `talkflow_states.current_node` for the active talk (JOIN), or `Talk.treeflow_id` fallback if state row absent. Order by `last_message_at DESC NULLS LAST`. `before` cursor = `last_message_at < :before`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_inbox_repository.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from ai_sdr.models.inbound_message import InboundMessageRow
from ai_sdr.models.lead import Lead
from ai_sdr.models.tenant import Tenant
from ai_sdr.repositories.inbox_repository import derive_state, list_contacts

pytestmark = pytest.mark.integration


def test_derive_state_pure():
    class T:  # minimal active-talk stand-in
        def __init__(self, status, hm): self.status, self.handling_mode = status, hm
    assert derive_state(None) == "awaiting"
    assert derive_state(T("active", "ai")) == "ai"
    assert derive_state(T("requires_review", "ai")) == "requires_review"
    assert derive_state(T("active", "human")) == "human"


async def test_list_contacts_includes_lead_without_talk(db_session):
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", display_name="T", architecture_version=2)
    db_session.add(tenant)
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant.id)})

    lead = Lead(tenant_id=tenant.id, whatsapp_e164="+5511999", status="pending_assignment", inbound_channel_label="main")
    db_session.add(lead)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(InboundMessageRow(
        tenant_id=tenant.id, provider="whatsapp_cloud", external_id=f"e-{uuid.uuid4().hex[:8]}",
        lead_id=lead.id, from_address="+5511999", text="oi quero registrar",
        received_at=now, raw={"body": "oi"}, status="queued", media_type="text",
    ))
    await db_session.flush()

    contacts = await list_contacts(
        db_session, tenant_id=tenant.id, channel_label="main", user_id=uuid.uuid4()
    )
    assert len(contacts) == 1
    c = contacts[0]
    assert c.lead_id == lead.id
    assert c.state == "awaiting"          # no Talk → awaiting
    assert c.last_message_preview.startswith("oi")
    assert c.unread == 1                  # no read marker → 1 unread
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_inbox_repository.py -q`
Expected: FAIL — `ModuleNotFoundError: ai_sdr.repositories.inbox_repository`.

- [ ] **Step 3: Write the repository**

Implement `inbox_repository.py` with `derive_state`, `ContactRow`/`MessageRow` dataclasses, `list_contacts`, and `list_messages` per the guidance above. `derive_state`:

```python
def derive_state(active_talk) -> str:
    if active_talk is None:
        return "awaiting"
    if active_talk.status == "requires_review":
        return "requires_review"
    if active_talk.handling_mode == "human":
        return "human"
    if active_talk.status == "active":
        return "ai"
    return "closed"
```

Write `list_contacts` / `list_messages` as real SQLAlchemy queries (Lead-anchored, correlated subqueries for last-message + unread, LEFT JOIN active Talk + talkflow_states for funnel_node). Keep the cursor (`before` on `last_message_at`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_inbox_repository.py -q`
Expected: PASS (2 passed). The lead-without-Talk row must appear with `state="awaiting"`, `unread=1`.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/inbox_repository.py tests/integration/test_inbox_repository.py
git commit -m "feat(chat-be): inbox read repository (lead-anchored contacts + messages + state)"
```

---

### Task 5: Read API router (instances, contacts, contact detail, messages, read) + wire into app

**Files:**
- Create: `src/ai_sdr/api/routes/console_inbox.py`
- Modify: `src/ai_sdr/main.py` (register `console_inbox.router`)
- Test: `tests/integration/test_console_inbox_routes.py`

**Interfaces:**
- Consumes: `require_tenant_access` (auth+RLS dep, returns `(Tenant, User)`), the inbox repository (T4), the schemas (T3), `Instance` (T1), `OperatorReadMarker` (T2).
- Produces routes (all behind `require_tenant_access`, tenant in the path for RLS):
  ```
  GET  /api/console/tenants/{tenant_slug}/instances                         -> list[InstanceOut]
  GET  /api/console/tenants/{tenant_slug}/instances/{instance_id}/contacts  -> list[ContactOut]   (?status=&funnel=&q=&before=)
  GET  /api/console/tenants/{tenant_slug}/contacts/{lead_id}                -> ContactDetailOut
  GET  /api/console/tenants/{tenant_slug}/contacts/{lead_id}/messages       -> list[MessageOut]   (?before=)
  POST /api/console/tenants/{tenant_slug}/contacts/{lead_id}/read           -> 204                (body ReadBody)
  ```

> Each route depends on `Annotated[tuple[Tenant, User], Depends(require_tenant_access)]` (this resolves the tenant from `{tenant_slug}`, enforces access, and sets RLS). The contacts route loads the `Instance` by id (RLS-scoped) → uses its `channel_label` → calls `list_contacts(...)`. The detail route computes the 24h window (`window_expires_at = max(inbound.received_at)+24h`, `window_open = now < that`). `ai_reasoning` is `None` for now (persisted reasoning lands in Plano 2). The read route upserts an `OperatorReadMarker` for `(user.id, lead_id)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_console_inbox_routes.py
"""Auth/RLS scaffolding mirrors tests/integration/test_console_leads_page.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_list_instances_returns_main(authed_inbox_client):
    client, ctx = authed_inbox_client  # fixture: signed-in client + seeded tenant w/ console enabled
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/instances")
    assert resp.status_code == 200
    labels = [i["channel_label"] for i in resp.json()]
    assert "main" in labels


async def test_contacts_lists_lead_without_talk(authed_inbox_client):
    client, ctx = authed_inbox_client
    # ctx seeded a lead 'pending_assignment' with one queued inbound on channel 'main'
    instances = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in instances if i["channel_label"] == "main")
    resp = await client.get(f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts")
    assert resp.status_code == 200
    body = resp.json()
    assert any(c["state"] == "awaiting" and c["unread"] >= 1 for c in body)
```

> **Build the `authed_inbox_client` fixture by mirroring `tests/integration/test_console_leads_page.py`**: it must (a) seed a `Tenant` whose on-disk config has `console.enabled=true` (or monkeypatch `ai_sdr.web.deps.tenant_loader_dep`), (b) seed a `User` + `UserTenantAccess`, (c) sign the `pesdr_session` cookie via `ai_sdr.web.auth.sign_session_cookie(user.id)` and set it on an httpx `AsyncClient(app=app)`, (d) seed a `Lead` (pending_assignment, channel 'main') + one queued `InboundMessageRow`, and the `'main'` `Instance` (the 0032 backfill only covers tenants that existed at migration time — seed the instance explicitly here). Return `(client, ctx)` with `ctx['slug']`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_console_inbox_routes.py -q`
Expected: FAIL — route 404 (router not registered) / fixture missing.

- [ ] **Step 3: Write the router + register it**

Implement `console_inbox.py` with the 5 routes per the interface. Each handler signature uses `ctx: Annotated[tuple[Tenant, User], Depends(require_tenant_access)]` and `db: Annotated[AsyncSession, Depends(db_session)]`. Register in `main.py` next to the other routers: `app.include_router(console_inbox.router)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_console_inbox_routes.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_inbox.py src/ai_sdr/main.py tests/integration/test_console_inbox_routes.py
git commit -m "feat(chat-be): contact-based inbox read API (instances/contacts/messages/read)"
```

---

### Task 6: Filters (status / funnel / search) + messages pagination + read→unread integration

**Files:**
- Modify: `src/ai_sdr/repositories/inbox_repository.py` (apply `status`, `funnel`, `q` filters; messages cursor)
- Modify: `src/ai_sdr/api/routes/console_inbox.py` (pass query params through)
- Test: `tests/integration/test_inbox_filters.py`

**Interfaces:**
- Consumes: T4 repository, T5 routes.
- Produces: `status` filter (one of `awaiting|ai|requires_review|human|closed`, applied via `derive_state` semantics → SQL predicates on the active talk / absence); `funnel` filter (active talk's `treeflow_id == funnel`); `q` (ILIKE on `display_name`/`whatsapp_e164`); messages `before` cursor; and read→unread roundtrip (POST read then list shows `unread=0`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_inbox_filters.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_status_filter_awaiting(authed_inbox_client):
    client, ctx = authed_inbox_client
    insts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in insts if i["channel_label"] == "main")
    resp = await client.get(
        f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts?status=awaiting"
    )
    assert resp.status_code == 200
    assert all(c["state"] == "awaiting" for c in resp.json())


async def test_read_then_unread_zero(authed_inbox_client):
    client, ctx = authed_inbox_client
    lead_id = ctx["lead_id"]
    msgs = (await client.get(f"/api/console/tenants/{ctx['slug']}/contacts/{lead_id}/messages")).json()
    latest = max(m["at"] for m in msgs)
    r = await client.post(
        f"/api/console/tenants/{ctx['slug']}/contacts/{lead_id}/read",
        json={"last_read_message_at": latest},
    )
    assert r.status_code == 204
    insts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances")).json()
    main_id = next(i["id"] for i in insts if i["channel_label"] == "main")
    contacts = (await client.get(f"/api/console/tenants/{ctx['slug']}/instances/{main_id}/contacts")).json()
    c = next(c for c in contacts if c["lead_id"] == lead_id)
    assert c["unread"] == 0
```

> Extend the `authed_inbox_client` fixture (Task 5) to also expose `ctx["lead_id"]`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_inbox_filters.py -q`
Expected: FAIL — status param ignored / unread not zeroed.

- [ ] **Step 3: Implement filters + read upsert wiring**

Add the `status`/`funnel`/`q` predicates to `list_contacts`; ensure the read route upserts the marker and the unread subquery reads it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_inbox_filters.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/repositories/inbox_repository.py src/ai_sdr/api/routes/console_inbox.py tests/integration/test_inbox_filters.py
git commit -m "feat(chat-be): inbox filters (status/funnel/search) + read→unread"
```

---

### Task 7: Performance indexes + full read-suite green

**Files:**
- Create: `migrations/versions/0034_inbox_indexes.py`
- Test: re-run the inbox integration suite

**Interfaces:**
- Produces indexes serving the inbox queries: `leads (tenant_id, inbound_channel_label, created_at DESC)`; `inbound_messages (lead_id, received_at DESC)`; `outbound_messages (lead_id, sent_at DESC)`; `talks (tenant_id, lead_id, status)`.

- [ ] **Step 1: Write the migration**

```python
# migrations/versions/0034_inbox_indexes.py
"""Indexes for the contact-based inbox read paths.

Revision ID: 0034_inbox_indexes
Revises: 0033_operator_read_markers
"""

from alembic import op

revision = "0034_inbox_indexes"
down_revision = "0033_operator_read_markers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_leads_tenant_channel_created", "leads", ["tenant_id", "inbound_channel_label", "created_at"])
    op.create_index("ix_inbound_lead_received", "inbound_messages", ["lead_id", "received_at"])
    op.create_index("ix_outbound_lead_sent", "outbound_messages", ["lead_id", "sent_at"])
    op.create_index("ix_talks_tenant_lead_status", "talks", ["tenant_id", "lead_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_talks_tenant_lead_status", "talks")
    op.drop_index("ix_outbound_lead_sent", "outbound_messages")
    op.drop_index("ix_inbound_lead_received", "inbound_messages")
    op.drop_index("ix_leads_tenant_channel_created", "leads")
```

- [ ] **Step 2: Apply + run the whole inbox suite**

Run: `uv run alembic upgrade head` (expect `0034_inbox_indexes`), then:
`uv run pytest tests/integration/test_instances_model.py tests/integration/test_read_markers_model.py tests/integration/test_inbox_repository.py tests/integration/test_console_inbox_routes.py tests/integration/test_inbox_filters.py -q`
Expected: PASS (all green).

- [ ] **Step 3: Commit**

```bash
git add migrations/versions/0034_inbox_indexes.py
git commit -m "feat(chat-be): inbox read-path indexes"
```

---

## Self-Review

**Spec coverage (spec §-by-§ → task):**
- §3/§6 instância materializada → Task 1. §6 read-state por contato → Task 2. §6 `talk_id` opcional (reads computam) → Task 4 (não depende de talk_id). ✓
- §3.1 estados do contato (incl. "awaiting"/"closed" sem Talk; lead-anchored) → Task 4 `derive_state` + the lead-without-Talk test. ✓
- §11 API (instances/contacts/messages/read) → Tasks 5,6 (paths ganham `{tenant_slug}` p/ reusar `require_tenant_access` + RLS — refinamento documentado). ✓
- §10 auth/RLS (cookie + require_tenant_access, fixing the unauthed gap of the old `/tenants/.../leads` route) → Task 5. ✓
- Filtro de funil pelo Talk ativo (default §14) → Task 6. ✓
- Janela 24h computada → Task 5 detail route. ✓ (envio/templates = Plano 2.)
- §6.2 índices → Task 7. ✓

**Deferred to Plano 2 (write side), explicitly NOT in this plan:** denormalização de resumo no `leads` + worker writes; takeover/send/HITL; WS hub + `seq`; delivery-status (`statuses`); templates registry; persistir `ai_reasoning` (detail returns `None` for now). The read API computes from messages so it is correct + testable without the worker.

**Placeholder scan:** No "TBD". Two tasks (5, 6) instruct mirroring `test_console_leads_page.py` for the auth-cookie fixture rather than inventing it — deliberate (the console auth test is the source of truth for signing + console-enabled tenant config).

**Type consistency:** `ContactState` literal identical across schemas (T3) + `derive_state` (T4). `ContactOut`/`MessageOut`/`InstanceOut` field names match between T3 schemas and T5 routes. Migration chain 0032→0033→0034 linear off 0031.

## Open items the implementer resolves against live code
1. The `authed_inbox_client` fixture (cookie signing + console-enabled tenant) — mirror `tests/integration/test_console_leads_page.py` (Tasks 5,6).
2. `talkflow_states.current_node` join for `funnel_node` — confirm the column/table name against `models/talkflow_state.py`; fall back to `Talk.treeflow_id` if the state row is absent.
3. `OutboundMessageRow` class/module name — confirm import path in `repositories/inbox_repository.py`.
