"""Build a MessagingAdapter from MessagingConfig + resolved secrets.

Same dispatch pattern as `ai_sdr.llm.factory.build_llm` — providers are
registered in a dict keyed by the `provider` string from tenant.yaml.

The `secrets/` prefix on `*_ref` config fields is stripped before lookup,
matching the convention enforced by MessagingConfig's validator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ai_sdr.messaging.base import MessagingAdapter
from ai_sdr.messaging.fake import FakeMessagingAdapter
from ai_sdr.schemas.tenant_yaml import MessagingConfig

# Registry of (provider_name → builder callable).
# Builders take (cfg, secrets) and return a MessagingAdapter.
_REGISTRY: dict[str, Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]] = {}


def register_provider(
    name: str,
) -> Callable[
    [Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]],
    Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter],
]:
    """Decorator: registers a builder under `name`. Used by impl modules
    (whatsapp_cloud.py) so they don't have to import the factory."""

    def _wrap(
        builder: Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter],
    ) -> Callable[[MessagingConfig, Mapping[str, str]], MessagingAdapter]:
        if name in _REGISTRY:
            raise RuntimeError(f"messaging provider already registered: {name}")
        _REGISTRY[name] = builder
        return builder

    return _wrap


def _resolve_secret(ref: str | None, secrets: Mapping[str, str]) -> str | None:
    if ref is None:
        return None
    if not ref.startswith("secrets/"):
        raise ValueError(f"secret ref must start with 'secrets/' (got {ref!r})")
    bare = ref[len("secrets/") :]
    if bare not in secrets:
        raise KeyError(f"secret {bare!r} not present in resolved secrets")
    return secrets[bare]


# --- built-in registrations ---------------------------------------------------


@register_provider("fake")
def _build_fake(cfg: MessagingConfig, secrets: Mapping[str, str]) -> MessagingAdapter:
    return FakeMessagingAdapter()


def build_messaging_adapter(cfg: MessagingConfig, secrets: Mapping[str, str]) -> MessagingAdapter:
    # Importing whatsapp_cloud triggers its @register_provider("whatsapp_cloud")
    # side-effect. Done lazily so unit tests of the factory don't require
    # httpx/tenacity stacks just to dispatch to FakeMessagingAdapter.
    if "whatsapp_cloud" not in _REGISTRY:
        from ai_sdr.messaging import whatsapp_cloud  # noqa: F401

    builder = _REGISTRY.get(cfg.provider)
    if builder is None:
        raise ValueError(f"unknown messaging provider: {cfg.provider!r}")
    return builder(cfg, secrets)
