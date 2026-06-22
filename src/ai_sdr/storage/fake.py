"""In-memory StorageAdapter for tests + simulate CLI. No I/O."""

from __future__ import annotations

from ai_sdr.storage.base import StorageAdapter


class FakeStorageAdapter(StorageAdapter):
    def __init__(self, base_url: str = "https://fake.storage.local") -> None:
        self._base_url = base_url.rstrip("/")
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = data
        self.content_types[key] = content_type
        return f"{self._base_url}/{key}"

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return f"{self._base_url}/{key}"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)
