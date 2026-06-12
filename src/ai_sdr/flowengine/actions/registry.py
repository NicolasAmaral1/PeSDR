"""ActionAdapter registry (FE-03c §7.2).

Plug-and-play registration via @register decorator. Adding a new adapter:
1. Subclass ActionAdapter, set `name`, implement `execute`.
2. Decorate the class with @register.
3. Import the module in flowengine/actions/__init__.py (side-effect).
"""

from __future__ import annotations

from ai_sdr.flowengine.actions.base import ActionAdapter

ACTION_ADAPTERS: dict[str, type[ActionAdapter]] = {}


def register(adapter_cls: type[ActionAdapter]) -> type[ActionAdapter]:
    """Decorator: register an ActionAdapter under its `name` attribute."""
    name = getattr(adapter_cls, "name", None)
    if not name or not isinstance(name, str):
        raise ValueError(f"{adapter_cls.__name__} missing `name` class attribute")
    if name in ACTION_ADAPTERS:
        raise ValueError(f"adapter {name!r} already registered")
    ACTION_ADAPTERS[name] = adapter_cls
    return adapter_cls
