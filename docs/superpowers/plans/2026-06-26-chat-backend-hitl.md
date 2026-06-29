# Chat Backend — HITL State Machine (Plano 2A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operator inbox interactive — an operator can **take over** a conversation (pausing the AI) and **send** real WhatsApp messages (HITL), with the AI correctly suppressed for human-held talks. This closes the escalation gap.

**Architecture:** Add `talks.assigned_operator_id` + `outbound_messages.client_message_id` + extend the `triggered_by` CHECK for `'operator'`. Three new write routes on the existing `console_inbox` router (takeover/release/send), all behind `require_tenant_access` with explicit `tenant_id` scoping. The AI is gated by a single check inside `run_turn` (re-read `handling_mode` under the per-lead advisory lock → bail if `human`), plus `scan_talks` skipping human-held talks. Opt-out keywords are still honored in human mode.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, pytest (`pytest.mark.integration`), uv. Builds on Plano 1 (branch `dev/nicolas-chat-frontend`, DB at head `0034`).

## Global Constraints

- **Tenant safety (app role bypasses RLS):** every query filters `tenant_id` explicitly; every lead/talk-scoped route verifies `lead.tenant_id == tenant.id` (404) before acting. Same posture as Plano 1.
- **Migrations** chain off `0034_inbox_indexes`: `0035`, `0036` in order. RLS already exists on `talks`/`outbound_messages`.
- **Atomic takeover:** the handling_mode flip MUST be a single conditional UPDATE (`WHERE … handling_mode='ai' RETURNING`), never read-then-write. 0 rows → HTTP 409.
- **AI suppression invariant:** a talk with `handling_mode='human'` must NEVER get an AI reply. The gate lives inside `run_turn` (re-read under the advisory lock), so it also covers takeover-mid-turn races.
- **Operator send is its own path** — it calls `adapter.send_text` directly and inserts an `OutboundMessage` with `triggered_by='operator'`; it does NOT run `run_turn`.
- **Idempotency:** operator send carries a client-generated `client_message_id` (UUID); a duplicate POST with the same id returns the existing row, never re-sends.
- **Auth/RLS test scaffolding:** reuse the `authed_inbox_client` fixture in `tests/integration/conftest.py` (from Plano 1) — it yields `(client, ctx)` with `ctx['slug']`, `ctx['lead_id']`. Seed additional rows (a Talk) via the test DB session under `set_config('app.current_tenant', ...)`.
- **Env:** test DB ready (`.env` + tunnel @ localhost:15432, alembic head `0034`). If tunnel refused: `pkill -f "ssh.*15432"; ssh -fN -L 15432:localhost:15432 -L 16379:localhost:16379 vps-nova`. Run integration separately from unit.
- **TDD:** failing test → confirm fail → minimal impl → confirm pass → commit. Commit messages: `feat(chat-hitl): …`.

---

### Task 1: `talks.assigned_operator_id` column + model + migration (0035)

**Files:**
- Modify: `src/ai_sdr/models/talk.py` (add the field near `handling_mode`)
- Create: `migrations/versions/0035_talks_assigned_operator.py`
- Test: `tests/integration/test_talk_assigned_operator.py`

**Interfaces:**
- Produces: `Talk.assigned_operator_id: Mapped[uuid.UUID | None]` (FK `users.id` ON DELETE SET NULL, nullable). Records which operator owns a human-held talk.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_talk_assigned_operator.py
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from ai_sdr.models.talk import Talk

pytestmark = pytest.mark.integration


async def test_talk_has_assigned_operator_column(db_session, seeded_talk_factory):
    talk, tenant = await seeded_talk_factory(handling_mode="ai")
    talk.assigned_operator_id = None  # column exists, nullable
    await db_session.flush()
    refreshed = await db_session.get(Talk, talk.id)
    assert hasattr(refreshed, "assigned_operator_id")
    assert refreshed.assigned_operator_id is None
```

> `seeded_talk_factory` does not exist yet — add it to `tests/integration/conftest.py` as a fixture that seeds a Tenant + Lead + TreeflowVersion + Talk under tenant context and returns `(talk, tenant)`. Accept `handling_mode`/`status` kwargs (defaults `handling_mode="ai"`, `status="active"`). Mirror the Talk-seeding already present in `tests/integration/test_inbox_filters.py` (the multi-active-talk regression test seeds Talks the same way).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_talk_assigned_operator.py -q`
Expected: FAIL — `AttributeError: assigned_operator_id` / column missing.

- [ ] **Step 3: Add the model field + migration**

In `src/ai_sdr/models/talk.py`, after the `handling_mode` column:

```python
    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
```

```python
# migrations/versions/0035_talks_assigned_operator.py
"""talks.assigned_operator_id — which operator owns a human-held talk.

Revision ID: 0035_talks_assigned_operator
Revises: 0034_inbox_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_talks_assigned_operator"
down_revision = "0034_inbox_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "talks",
        sa.Column("assigned_operator_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_talks_assigned_operator", "talks", "users",
        ["assigned_operator_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_talks_assigned_operator", "talks", type_="foreignkey")
    op.drop_column("talks", "assigned_operator_id")
```

- [ ] **Step 4: Apply + run test**

Run: `uv run alembic upgrade head` (expect `0035_talks_assigned_operator`), then `uv run pytest tests/integration/test_talk_assigned_operator.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/talk.py migrations/versions/0035_talks_assigned_operator.py tests/integration/test_talk_assigned_operator.py tests/integration/conftest.py
git commit -m "feat(chat-hitl): talks.assigned_operator_id + seeded_talk_factory fixture"
```

---

### Task 2: `outbound_messages.client_message_id` + extend `triggered_by` for `'operator'` (0036)

**Files:**
- Modify: `src/ai_sdr/observability/outbound_audit.py` (extend `TriggeredBy` Literal)
- Create: `migrations/versions/0036_outbound_operator_send.py`
- Test: `tests/integration/test_outbound_operator_columns.py`

**Interfaces:**
- Produces: `outbound_messages.client_message_id UUID NULL` (idempotency key, unique-per-talk when set) + the CHECK `ck_outbound_triggered_by` extended to include `'operator'`. `TriggeredBy` Literal gains `"operator"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_outbound_operator_columns.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from ai_sdr.models.outbound_message import OutboundMessage

pytestmark = pytest.mark.integration


async def test_operator_outbound_row_allowed(db_session, seeded_talk_factory):
    talk, tenant = await seeded_talk_factory(handling_mode="human")
    row = OutboundMessage(
        tenant_id=tenant.id, talkflow_id=talk.id, lead_id=talk.lead_id,
        provider="whatsapp_cloud", message_type="text", body_text="oi do operador",
        status="sent", triggered_by="operator", client_message_id=uuid.uuid4(),
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    await db_session.flush()  # must NOT violate ck_outbound_triggered_by
    assert row.client_message_id is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_outbound_operator_columns.py -q`
Expected: FAIL — `client_message_id` unknown column / CHECK violation on `triggered_by='operator'`.

- [ ] **Step 3: Migration + add the model column + extend the Literal**

Confirm the OutboundMessage model has a `client_message_id` mapped column; if absent, add:
```python
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```
In `src/ai_sdr/observability/outbound_audit.py` change:
```python
TriggeredBy = Literal["inbound", "follow_up_scanner", "window_expired_recovery", "operator"]
```

```python
# migrations/versions/0036_outbound_operator_send.py
"""outbound_messages: client_message_id + allow triggered_by='operator'.

Revision ID: 0036_outbound_operator_send
Revises: 0035_talks_assigned_operator
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_outbound_operator_send"
down_revision = "0035_talks_assigned_operator"
branch_labels = None
depends_on = None

_OLD = "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery')"
_NEW = "triggered_by IN ('inbound', 'follow_up_scanner', 'window_expired_recovery', 'operator')"
# Confirm the CHECK constraint name in migration 0011 (grep ck_ ... triggered_by); use it below.
_CK = "ck_outbound_messages_triggered_by"


def upgrade() -> None:
    op.add_column("outbound_messages", sa.Column("client_message_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(
        "ux_outbound_client_message", "outbound_messages",
        ["talkflow_id", "client_message_id"],
        unique=True, postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.drop_constraint(_CK, "outbound_messages", type_="check")
    op.create_check_constraint(_CK, "outbound_messages", _NEW)


def downgrade() -> None:
    op.drop_constraint(_CK, "outbound_messages", type_="check")
    op.create_check_constraint(_CK, "outbound_messages", _OLD)
    op.drop_index("ux_outbound_client_message", "outbound_messages")
    op.drop_column("outbound_messages", "client_message_id")
```

> **Resolve against live code:** the exact CHECK constraint name — grep `migrations/versions/0011_outbound_messages.py` for the `name=` of the `triggered_by` check (it may be `ck_outbound_messages_triggered_by` or similar). Use the real name in `_CK`.

- [ ] **Step 4: Apply + run test**

Run: `uv run alembic upgrade head` (expect `0036_outbound_operator_send`), then `uv run pytest tests/integration/test_outbound_operator_columns.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/models/outbound_message.py src/ai_sdr/observability/outbound_audit.py migrations/versions/0036_outbound_operator_send.py tests/integration/test_outbound_operator_columns.py
git commit -m "feat(chat-hitl): outbound client_message_id + operator triggered_by"
```

---

### Task 3: Takeover + release routes (atomic check-and-set)

**Files:**
- Modify: `src/ai_sdr/api/routes/console_inbox.py` (add `takeover`/`release`)
- Test: `tests/integration/test_hitl_takeover.py`

**Interfaces:**
- Consumes: `require_tenant_access`, `Talk`.
- Produces:
  - `POST /api/console/tenants/{tenant_slug}/contacts/{lead_id}/takeover` → finds the lead's ACTIVE talk; atomic `UPDATE talks SET handling_mode='human', assigned_operator_id=:user WHERE id=:talk_id AND handling_mode='ai' RETURNING id`; 0 rows → 409; returns `{talk_id, handling_mode:"human"}`. 404 if no active talk / foreign lead.
  - `POST .../release` → `UPDATE … SET handling_mode='ai', assigned_operator_id=NULL WHERE id=:talk_id AND handling_mode='human' RETURNING id`; 409 if not human.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_hitl_takeover.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_takeover_then_double_takeover_409(authed_inbox_client, seeded_talk_factory):
    client, ctx = authed_inbox_client
    talk, _ = await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    slug, lead = ctx["slug"], ctx["lead_id"]
    r1 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    assert r1.status_code == 200
    assert r1.json()["handling_mode"] == "human"
    r2 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    assert r2.status_code == 409  # already human → conflict


async def test_release_back_to_ai(authed_inbox_client, seeded_talk_factory):
    client, ctx = authed_inbox_client
    await seeded_talk_factory(lead_id=ctx["lead_id"], handling_mode="ai")
    slug, lead = ctx["slug"], ctx["lead_id"]
    await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    r = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/release")
    assert r.status_code == 200
    assert r.json()["handling_mode"] == "ai"
```

> Extend `seeded_talk_factory` to accept an optional `lead_id=` (use the existing lead instead of creating one) so the seeded talk attaches to `ctx["lead_id"]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_hitl_takeover.py -q`
Expected: FAIL — routes 404 (not implemented).

- [ ] **Step 3: Implement the routes**

Add to `console_inbox.py`. The takeover handler: load the lead (404 if foreign), find the active talk (`status IN ('active','requires_review')`, latest), then run the atomic UPDATE with `.returning(Talk.id)`; if no row → `HTTPException(409)`. Release mirrors it with the `handling_mode='human'` guard.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_hitl_takeover.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_inbox.py tests/integration/test_hitl_takeover.py
git commit -m "feat(chat-hitl): takeover (atomic) + release routes"
```

---

### Task 4: Operator send route (HITL, idempotent, 24h-aware)

**Files:**
- Modify: `src/ai_sdr/api/routes/console_inbox.py` (add `send`)
- Test: `tests/integration/test_hitl_send.py`

**Interfaces:**
- Consumes: `require_tenant_access`, `adapter_registry` dep (`registry.get_for_tenant(tenant)` → `MessagingAdapter`), `Talk`, `OutboundMessage`.
- Produces: `POST /api/console/tenants/{tenant_slug}/contacts/{lead_id}/send` body `{ text, client_message_id }` → requires the lead's active talk be `handling_mode='human'` (else 409); idempotent on `client_message_id` (if a row exists for `(talk_id, client_message_id)`, return it, don't re-send); else `adapter.send_text(lead.whatsapp_e164, text)` → insert `OutboundMessage(triggered_by='operator', client_message_id=…, talkflow_id=talk.id, message_type='text', body_text=text, status='sent', external_id=send_result.external_id, sent_at=now)`. On `WindowExpiredError` → 422 `{detail:"24h window closed; template required"}`. Returns `{outbound_id, external_id, status}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_hitl_send.py
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration


async def test_send_requires_human_then_sends_idempotent(authed_inbox_client_with_fake_adapter, seeded_talk_factory):
    client, ctx = authed_inbox_client_with_fake_adapter  # app.state.adapter_registry → FakeMessagingAdapter
    slug, lead = ctx["slug"], ctx["lead_id"]
    await seeded_talk_factory(lead_id=lead, handling_mode="ai")
    cmid = str(uuid.uuid4())
    body = {"text": "oi João, sou o operador", "client_message_id": cmid}

    r_ai = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body)
    assert r_ai.status_code == 409  # ai mode → must takeover first

    await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/takeover")
    r1 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body)
    assert r1.status_code == 200
    first_id = r1.json()["outbound_id"]
    r2 = await client.post(f"/api/console/tenants/{slug}/contacts/{lead}/send", json=body)  # same cmid
    assert r2.status_code == 200
    assert r2.json()["outbound_id"] == first_id  # idempotent, no re-send
```

> Build `authed_inbox_client_with_fake_adapter` from `authed_inbox_client` but set `app.state.adapter_registry` to a stub whose `get_for_tenant(tenant)` returns a `FakeMessagingAdapter` (from `ai_sdr.messaging.fake`). The fake's `send_text` records the send and returns a `SendResult`. Lead must have a `whatsapp_e164` (the Plano-1 fixture seeds one).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_hitl_send.py -q`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement the send route**

Add to `console_inbox.py`: load lead (404 if foreign) → active talk → require `handling_mode=='human'` (else 409) → dedup by `(talk.id, client_message_id)` (return existing) → `adapter = registry.get_for_tenant(tenant)`; `try: result = await adapter.send_text(lead.whatsapp_e164, body.text)` `except WindowExpiredError: 422` → insert OutboundMessage → commit → return.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_hitl_send.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/api/routes/console_inbox.py tests/integration/test_hitl_send.py
git commit -m "feat(chat-hitl): operator send route (idempotent, 24h-aware)"
```

---

### Task 5: AI gate — `run_turn` bails when handling_mode is human (under the lock)

**Files:**
- Modify: `src/ai_sdr/flowengine/pipeline.py` (`run_turn`)
- Test: `tests/integration/test_run_turn_human_gate.py`

**Interfaces:**
- Consumes: `run_turn`, `RunTurnResult`, `Talk`.
- Produces: inside `run_turn`, AFTER preprocessing resolves `ctx.talk` and the per-lead advisory lock is acquired (re-read the talk so a concurrent takeover is seen), **if `ctx.talk.handling_mode == 'human'` → return `RunTurnResult(outcome="skipped_human", current_node_after=state.current_node, response_text=None)` WITHOUT building the prompt, calling the LLM, or sending.** (Add `"skipped_human"` to the outcome strings.)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_run_turn_human_gate.py
"""When the active talk is human-held, run_turn must NOT call the LLM or send."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_run_turn_skips_when_human(db_session, seeded_talk_factory, run_turn_human_harness):
    # harness: builds tenant/treeflow/inbound for a lead whose ACTIVE talk is handling_mode='human';
    # llm is a stub that RAISES if invoked; adapter is a FakeMessagingAdapter.
    result, adapter, llm_called = await run_turn_human_harness(handling_mode="human")
    assert result.outcome == "skipped_human"
    assert not adapter.sent_messages   # nothing sent
    assert llm_called.value is False   # LLM never invoked
```

> Build `run_turn_human_harness` by mirroring `tests/integration/test_turn_voice_e2e.py` (the existing run_turn E2E harness): seed tenant+treeflow+lead+inbound, pre-create the lead's active Talk with `handling_mode='human'`, pass a stub `llm` whose `ainvoke` sets a flag/raises, and a `FakeMessagingAdapter`. Assert the gate fires.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_run_turn_human_gate.py -q`
Expected: FAIL — `run_turn` runs the turn / outcome is `"sent"`.

- [ ] **Step 3: Implement the gate**

In `pipeline.py`, inside `run_turn`, after `state = await state_repo.load(ctx.talk.id)` (the talk + state are loaded under the advisory lock), add:

```python
        # HITL gate: a human-held talk must never get an AI reply. Re-read here
        # (under the lead lock) so a takeover that landed mid-turn is honored.
        if ctx.talk.handling_mode == "human":
            logger.info("run_turn.skipped_human talk=%s", ctx.talk.id)
            return RunTurnResult(
                outcome="skipped_human",
                current_node_after=state.current_node,
                response_text=None,
            )
```

> Place it BEFORE the prompt build (`build_cached_layer`) and the LLM call.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_run_turn_human_gate.py -q`
Then re-run the existing smoke: `uv run pytest tests/integration/test_pipeline_smoke_3_turns.py -q` (must still pass — default talks are `ai`).
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/pipeline.py tests/integration/test_run_turn_human_gate.py
git commit -m "feat(chat-hitl): run_turn gates on human handling_mode (AI suppression)"
```

---

### Task 6: Opt-out still honored in human mode

**Files:**
- Modify: `src/ai_sdr/flowengine/pipeline.py` (`run_turn` — evaluate opt-out before the human gate)
- Test: `tests/integration/test_human_optout.py`

**Interfaces:**
- Consumes: the opt-out detection already in preprocessing (`OptOutDetected` / `_match_opt_out`).
- Produces: opt-out keywords are evaluated even when `handling_mode=='human'`. Today `resolve_pipeline_context` raises `OptOutDetected` BEFORE the talk is built, so opt-out already short-circuits regardless of handling_mode — **verify this ordering**; if opt-out detection happens to sit after the human gate, move the gate so opt-out wins. The test pins the behavior: a human-held talk receiving an opt-out keyword closes (`outcome="opt_out"`), it does NOT return `skipped_human`.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_human_optout.py
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_optout_wins_over_human_gate(run_turn_human_harness):
    # inbound text is an opt-out keyword; talk is human-held.
    result, adapter, _ = await run_turn_human_harness(handling_mode="human", inbound_text="sair")
    assert result.outcome == "opt_out"   # opt-out wins; NOT skipped_human
```

> Extend `run_turn_human_harness` to accept `inbound_text` and to configure the tenant's `opt_out_keywords` (e.g. `["sair"]`).

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/integration/test_human_optout.py -q`
Expected: If opt-out detection already precedes the gate (preprocessing raises first), this may PASS immediately — that is the desired state; keep the test as a regression pin and proceed. If it FAILS (gate returns `skipped_human`), reorder so opt-out is evaluated first.

- [ ] **Step 3: Ensure ordering (only if the test failed)**

If needed, make `run_turn` evaluate opt-out (call `_match_opt_out` on `inbound_text` against `opt_out_keywords`) before the human gate, raising/returning `outcome="opt_out"`. (If preprocessing already raises `OptOutDetected` before talk resolution, no code change is needed — just the regression test.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_human_optout.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/flowengine/pipeline.py tests/integration/test_human_optout.py
git commit -m "feat(chat-hitl): opt-out honored even in human mode (regression-pinned)"
```

---

### Task 7: `scan_talks` skips human-held talks

**Files:**
- Modify: `src/ai_sdr/worker/jobs/scan_talks.py`
- Test: `tests/integration/test_scan_skips_human.py`

**Interfaces:**
- Produces: `scan_active_talks` only considers `Talk.status == "active" AND Talk.handling_mode == "ai"` (Phase A query at line ~67) and the Phase B re-check (line ~112). A human-held talk is never auto-closed by inactivity/duration.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_scan_skips_human.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ai_sdr.worker.jobs.scan_talks import scan_active_talks

pytestmark = pytest.mark.integration


async def test_scan_does_not_close_human_talk(db_session, seeded_talk_factory):
    # human-held talk, last_message_at long ago → would normally close by inactivity.
    old = datetime.now(timezone.utc) - timedelta(days=30)
    talk, _ = await seeded_talk_factory(handling_mode="human", status="active")
    talk.last_message_at = old
    await db_session.commit()
    await scan_active_talks(db_session, now=datetime.now(timezone.utc))
    refreshed = await db_session.get(type(talk), talk.id)
    assert refreshed.status == "active"   # NOT closed_inactivity
```

> Confirm `scan_active_talks(session, *, now)` signature against `scan_talks.py`; adapt the call (it may also need a treeflow/config — mirror the existing `tests/integration/test_scan_talks.py` setup if the bare call needs more).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_scan_skips_human.py -q`
Expected: FAIL — the human talk gets `closed_inactivity`.

- [ ] **Step 3: Add the handling_mode guard**

In `scan_talks.py`, add `Talk.handling_mode == "ai"` to the Phase A `.where(Talk.status == "active")` (line ~67) and to the Phase B re-check (`.where(Talk.id == talk_id, Talk.status == "active")` line ~112).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_scan_skips_human.py -q`
Then re-run `uv run pytest tests/integration/test_scan_talks.py -q` (existing — ai talks still close).
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_sdr/worker/jobs/scan_talks.py tests/integration/test_scan_skips_human.py
git commit -m "feat(chat-hitl): scan_talks skips human-held talks"
```

---

### Task 8: Full HITL suite green + migration head check

**Files:**
- Test: re-run the HITL + Plano-1 suites

**Interfaces:** none (verification task).

- [ ] **Step 1: Run the full HITL + inbox suites**

Run:
```
uv run pytest tests/integration/test_talk_assigned_operator.py tests/integration/test_outbound_operator_columns.py \
  tests/integration/test_hitl_takeover.py tests/integration/test_hitl_send.py \
  tests/integration/test_run_turn_human_gate.py tests/integration/test_human_optout.py \
  tests/integration/test_scan_skips_human.py \
  tests/integration/test_console_inbox_routes.py tests/integration/test_inbox_filters.py -q
```
Expected: all PASS.

- [ ] **Step 2: Confirm migration head + no regressions on the v2 turn**

Run: `uv run alembic current` (expect `0036_outbound_operator_send`), then `uv run pytest tests/integration/test_pipeline_smoke_3_turns.py tests/integration/test_turn_voice_e2e.py -q` (the AI turn + voice E2E still work — default talks are `ai`).
Expected: PASS.

- [ ] **Step 3: Commit (if any test-only fixups were needed)**

```bash
git add tests/integration/conftest.py
git commit -m "test(chat-hitl): HITL suite green"
```

---

## Self-Review

**Spec coverage (spec §7 HITL → task):**
- `assigned_operator_id` → Task 1. `triggered_by='operator'` + `client_message_id` → Task 2. ✓
- Takeover atômico (check-and-set, 409) + release → Task 3. ✓
- Operator send (direct adapter, triggered_by=operator, idempotent, 24h→422) → Task 4. ✓
- `run_turn` re-reads handling_mode under the lock → human → no AI reply → Task 5. ✓
- Opt-out honored in human mode → Task 6. ✓
- `scan_talks` skips human → Task 7. ✓
- `send` requires human (or 409) → Task 4. ✓

**Deferred to Plano 2B (NOT here):** WebSocket hub + Redis pub/sub + event `seq`; delivery-status (`statuses` webhook, ✓✓); `whatsapp_templates` registry + template send when window closed; `contact.updated`/`talk.updated` publishes; send-when-no-active-talk opening a Talk (2A requires an existing active talk — operator must have a conversation to take over; cold proactive send is 2B with templates).

**Placeholder scan:** No "TBD". Several tasks point at existing tests/fixtures to mirror (`seeded_talk_factory`, `authed_inbox_client`, `run_turn_human_harness` from `test_turn_voice_e2e.py`, `test_scan_talks.py`) rather than inventing harnesses — deliberate; those are the source-of-truth patterns. The CHECK constraint name (Task 2) and `scan_active_talks` signature (Task 7) are explicit "resolve against live code" items.

**Type consistency:** `handling_mode` values `'ai'`/`'human'` consistent across Tasks 1,3,4,5,7. `triggered_by='operator'` consistent (Task 2 Literal + migration CHECK + Task 4 insert). `RunTurnResult(outcome="skipped_human")` defined in Task 5, asserted in Tasks 5/6. Migration chain 0035→0036 linear off 0034.

## Open items the implementer resolves against live code
1. `seeded_talk_factory` + `run_turn_human_harness` + `authed_inbox_client_with_fake_adapter` fixtures — build in `tests/integration/conftest.py`, mirroring `test_inbox_filters.py` (Talk seeding), `test_turn_voice_e2e.py` (run_turn harness), and the Plano-1 `authed_inbox_client`.
2. The `triggered_by` CHECK constraint name in `0011_outbound_messages.py` (Task 2 `_CK`).
3. `scan_active_talks(...)` exact signature + whether the bare call needs treeflow/config (Task 7).
4. Confirm `OutboundMessage` already has (or add) `client_message_id` (Task 2).
