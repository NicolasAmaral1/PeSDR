"""Action parameter templating (FE-03c §8).

Jinja2 sandboxed environment. Renders dict/list/str recursively, scalars
passthrough. Undefined variables and sandbox violations raise
TemplateRenderError, which the dispatcher catches and logs as
`action.dispatch.template_render_failed`.
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment


class TemplateRenderError(Exception):
    """Wraps any Jinja2 render-time failure (undefined, sandbox, etc)."""


_ENV = SandboxedEnvironment(
    autoescape=False,
    undefined=StrictUndefined,
)


def render_params(template: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Render strings recursively; dicts/lists traversed; scalars passthrough."""

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return _ENV.from_string(value).render(**context)
            except TemplateError as exc:
                raise TemplateRenderError(str(exc)) from exc
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    rendered = walk(template)
    if not isinstance(rendered, dict):
        raise TemplateRenderError(
            f"top-level template must be a dict, got {type(rendered).__name__}"
        )
    return rendered


def build_template_context(
    state: Any, decision: Any, lead: Any, talk: Any
) -> dict[str, Any]:
    """Build the whitelisted context dict exposed to Jinja2.

    Whitelist scope: only fields adapters are expected to need.
    Notably excludes lead.tenant_id (security) and full ORM objects
    (avoid lazy-load surprises in the sandbox).
    """
    merged_collected = {**state.collected, **decision.collected_fields}
    return {
        "collected": merged_collected,
        "extracted_facts": state.extracted_facts,
        "lead": {
            "id": str(lead.id),
            "whatsapp_e164": lead.whatsapp_e164,
            "external_label": lead.external_label,
        },
        "talk": {
            "id": str(talk.id),
            "treeflow_id": talk.treeflow_id,
            "turn_count": talk.turn_count,
        },
    }
