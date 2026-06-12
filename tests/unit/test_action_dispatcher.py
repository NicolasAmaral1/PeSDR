"""dispatch_actions: idempotency + template/duplicate skip paths (FE-03c Task 11)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
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
        id=uuid4(),
        tenant_id=uuid4(),
        treeflow_id="tf",
        turn_count=1,
    )


def _make_lead():
    return SimpleNamespace(
        id=uuid4(),
        whatsapp_e164="+5511999",
        external_label="x",
    )


@pytest.mark.asyncio
async def test_dispatch_skipped_when_field_not_in_collected_fields():
    """LLM didn't emit demo_data this turn → action does NOT enqueue."""
    actions = [
        OnCollectedAction(
            field="demo_data",
            adapter="logging",
            handler="schedule_event",
            params={"title": "hi"},
        )
    ]
    node = _make_node(actions)
    state = _make_state()
    decision = _make_decision(collected_fields={"nome": "joana"})
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
    actions = [
        OnCollectedAction(
            field="demo_data",
            adapter="logging",
            handler="schedule_event",
            params={"title": "Demo {{ collected.nome }}"},
        )
    ]
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
    actions = [
        OnCollectedAction(
            field="demo_data",
            adapter="logging",
            handler="schedule_event",
            params={"title": "x"},
        )
    ]
    node = _make_node(actions)
    decision = _make_decision(collected_fields={"demo_data": "2026-06-13"})

    repo = MagicMock()
    repo.insert_pending = AsyncMock(return_value=None)
    enqueue = AsyncMock()

    with caplog.at_level(logging.INFO):
        await dispatch_actions(
            session=MagicMock(),
            repo=repo,
            enqueue=enqueue,
            state=_make_state(),
            decision=decision,
            node_spec=node,
            talk=_make_talk(),
            lead=_make_lead(),
        )
    enqueue.assert_not_awaited()
    assert any("skipped_duplicate" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_template_error_logs_and_skips(caplog):
    actions = [
        OnCollectedAction(
            field="demo_data",
            adapter="logging",
            handler="x",
            params={"title": "Hello {{ collected.missing }}"},
        )
    ]
    node = _make_node(actions)
    decision = _make_decision(collected_fields={"demo_data": "2026-06-13"})

    repo = MagicMock()
    repo.insert_pending = AsyncMock()
    enqueue = AsyncMock()

    with caplog.at_level(logging.WARNING):
        await dispatch_actions(
            session=MagicMock(),
            repo=repo,
            enqueue=enqueue,
            state=_make_state(),
            decision=decision,
            node_spec=node,
            talk=_make_talk(),
            lead=_make_lead(),
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
        session=MagicMock(),
        repo=repo,
        enqueue=enqueue,
        state=_make_state(),
        decision=_make_decision(collected_fields={"x": 1}),
        node_spec=node,
        talk=_make_talk(),
        lead=_make_lead(),
    )
    repo.insert_pending.assert_not_awaited()
