"""Build a FormProviderAdapter from FormProviderConfig + resolved secrets.

Same dispatch pattern as `ai_sdr.messaging.factory.build_messaging_adapter`.
Providers register their builder via the `@register_provider("name")`
decorator at module load time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.forms.base import FormProviderAdapter
from ai_sdr.schemas.tenant_yaml import FormProviderConfig

_REGISTRY: dict[
    str, Callable[[FormProviderConfig, Mapping[str, str]], FormProviderAdapter]
] = {}


def register_provider(
    name: str,
) -> Callable[
    [Callable[[FormProviderConfig, Mapping[str, str]], FormProviderAdapter]],
    Callable[[FormProviderConfig, Mapping[str, str]], FormProviderAdapter],
]:
    """Decorator: register a builder under `name`."""

    def _wrap(
        builder: Callable[[FormProviderConfig, Mapping[str, str]], FormProviderAdapter],
    ) -> Callable[[FormProviderConfig, Mapping[str, str]], FormProviderAdapter]:
        if name in _REGISTRY:
            raise RuntimeError(f"form provider already registered: {name}")
        _REGISTRY[name] = builder
        return builder

    return _wrap


def resolve_secret(ref: str | None, secrets: Mapping[str, str]) -> str | None:
    """Resolve a `secrets/...` ref against the loaded secrets dict.

    Re-exported here so adapter modules don't have to import the messaging
    factory just for this helper.
    """
    if ref is None:
        return None
    if not ref.startswith("secrets/"):
        raise ValueError(f"secret ref must start with 'secrets/' (got {ref!r})")
    bare = ref[len("secrets/") :]
    if bare not in secrets:
        raise KeyError(f"secret {bare!r} not present in resolved secrets")
    return secrets[bare]


def build_form_adapter(
    provider: str,
    cfg: FormProviderConfig,
    secrets: Mapping[str, str],
) -> FormProviderAdapter:
    """Build the adapter for the named provider.

    Imports `forms.respondi` lazily so unit tests of the factory don't
    require the full Respondi parsing stack.
    """
    if provider not in _REGISTRY and provider == "respondi":
        # Lazy import — triggers @register_provider side-effect.
        from ai_sdr.forms import respondi  # noqa: F401

    builder = _REGISTRY.get(provider)
    if builder is None:
        raise ValueError(f"unknown form provider: {provider!r}")
    return builder(cfg, secrets)
