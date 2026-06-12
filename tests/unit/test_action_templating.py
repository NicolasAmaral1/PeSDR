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
