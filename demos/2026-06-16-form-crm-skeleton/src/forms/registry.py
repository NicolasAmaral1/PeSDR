"""Registry de FormProviderAdapters.

Pattern idêntico ao `messaging/registry.py` e `flowengine/actions/registry.py`.
Adapter declara `@register` no topo da classe; side-effect import em
`forms/__init__.py` popula o registry no startup.
"""
from __future__ import annotations

from ai_sdr.forms.base import FormProviderAdapter

# Dict global: name (snake_case) → class (não instância)
FORM_PROVIDERS: dict[str, type[FormProviderAdapter]] = {}


def register(adapter_cls: type[FormProviderAdapter]) -> type[FormProviderAdapter]:
    """Decorator pra registrar FormProviderAdapter no FORM_PROVIDERS dict.

    Uso:
        @register
        class RespondiFormAdapter(FormProviderAdapter):
            name = "respondi"
            ...

    Raises:
        ValueError: se `name` class attribute ausente ou já registrado.
    """
    # TODO: implementação real
    # if not getattr(adapter_cls, "name", None):
    #     raise ValueError(f"{adapter_cls.__name__} missing `name` class attribute")
    # if adapter_cls.name in FORM_PROVIDERS:
    #     raise ValueError(f"form provider {adapter_cls.name!r} already registered")
    # FORM_PROVIDERS[adapter_cls.name] = adapter_cls
    # return adapter_cls
    raise NotImplementedError("Fase A T4 — registry decorator")
