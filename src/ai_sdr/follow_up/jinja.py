"""Render HSM template parameters via sandboxed Jinja2.

Each entry in `params` is a small Jinja string (typically `{{ ... }}`).
We render them independently against a context that exposes:
  - `collected` — TalkFlow's extracted fields dict (from LangGraph state)
  - `lead`      — Lead ORM row (whatsapp_e164, external_label)
  - `tenant`    — Tenant ORM row (slug, display_name)

The sandbox blocks dunder access and arbitrary attribute traversal,
so a malicious template author can't pivot to module imports.
StrictUndefined forces template authors to use `| default('...')`
for optional vars (avoids "Oi , tudo bem?" with awkward spacing).
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(
    autoescape=False,  # HSM params are plaintext, not HTML
    undefined=StrictUndefined,
)


def render_params(
    params: list[str],
    *,
    lead: Any,
    tenant: Any,
    collected: dict[str, Any],
) -> list[str]:
    """Render each param string in the context. Returns a list of the same
    length as `params`."""
    out: list[str] = []
    for p in params:
        template = _env.from_string(p)
        out.append(template.render(collected=collected, lead=lead, tenant=tenant))
    return out
