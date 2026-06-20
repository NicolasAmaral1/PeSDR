"""StorageAdapter contract — opaque blob storage for media (audio).

Keys are caller-chosen, deterministic per message id (e.g.
'inbound/{id}.ogg') so retries overwrite idempotently. Adapters know
nothing about tenants/leads; config+secrets injected at construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageAdapter(ABC):
    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under key; return a URL referencing the object."""

    @abstractmethod
    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a URL for an existing object (presigned if applicable)."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove the object. No-op if absent."""
