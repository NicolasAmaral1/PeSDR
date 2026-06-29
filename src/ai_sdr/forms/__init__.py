"""Form provider ingestion subsystem (spec 2026-06-16).

Boundary for ingesting one-shot lead origination from web forms (Respondi
pilot impl; Typeform/Jotform/etc as future impls). Conceptually parallel to
`messaging/`, but for forms instead of ongoing conversations.

Side-effect imports below register builders in the factory; downstream
callers just call `build_form_adapter(provider, cfg, secrets)` without
caring which module owns which provider.
"""

from __future__ import annotations

# Side-effect imports — register provider builders.
from ai_sdr.forms import respondi as _respondi  # noqa: F401
