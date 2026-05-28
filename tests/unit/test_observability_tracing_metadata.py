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
    lead_obj = SimpleNamespace()
    lead_obj.id = uuid.uuid4()
    return lead_obj


def test_minimal_only_trace_origin() -> None:
    m = build_trace_metadata(trace_origin="process_lead_inbox")
    assert m == {"trace_origin": "process_lead_inbox"}


def test_full_metadata() -> None:
    t = _tenant("joana")
    tf = _talkflow()
    lead_obj = _lead()
    m = build_trace_metadata(
        tenant=t, talkflow=tf, lead=lead_obj,
        node="qualificacao", turn_index=3,
        trace_origin="guardrails_critic",
    )
    assert m["trace_origin"] == "guardrails_critic"
    assert m["tenant_id"] == str(t.id)
    assert m["tenant_slug"] == "joana"
    assert m["talkflow_id"] == str(tf.id)
    assert m["lead_id"] == str(lead_obj.id)
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
