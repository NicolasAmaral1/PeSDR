"""Webhook routes accept optional /{channel_label} segment — multi-channel pre-paving."""

from __future__ import annotations

from ai_sdr.api.routes.webhooks import router


def _route_paths(method: str) -> list[str]:
    paths = []
    for r in router.routes:
        if not hasattr(r, "methods") or method not in r.methods:
            continue
        paths.append(r.path)
    return paths


def test_get_route_legacy_url_present():
    """Existing /webhooks/{tenant_slug}/{provider} GET still registered (backwards-compat)."""
    assert "/webhooks/{tenant_slug}/{provider}" in _route_paths("GET")


def test_get_route_with_channel_label_present():
    """New /webhooks/{tenant_slug}/{provider}/{channel_label} GET registered."""
    assert "/webhooks/{tenant_slug}/{provider}/{channel_label}" in _route_paths("GET")


def test_post_route_legacy_url_present():
    """Existing /webhooks/{tenant_slug}/{provider} POST still registered."""
    assert "/webhooks/{tenant_slug}/{provider}" in _route_paths("POST")


def test_post_route_with_channel_label_present():
    """New /webhooks/{tenant_slug}/{provider}/{channel_label} POST registered."""
    assert "/webhooks/{tenant_slug}/{provider}/{channel_label}" in _route_paths("POST")
