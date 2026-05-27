"""Sandboxed Jinja2 param rendering for HSM templates."""

from __future__ import annotations

import uuid
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
    with pytest.raises(Exception):
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
