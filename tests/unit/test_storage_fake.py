from __future__ import annotations

import pytest

from ai_sdr.storage.fake import FakeStorageAdapter


async def test_upload_then_get_url_roundtrips():
    s = FakeStorageAdapter(base_url="https://fake.local")
    url = await s.upload("outbound/123.ogg", b"\x00\x01", "audio/ogg")
    assert url == "https://fake.local/outbound/123.ogg"
    assert s.objects["outbound/123.ogg"] == b"\x00\x01"
    assert await s.get_url("outbound/123.ogg") == url


async def test_delete_removes_object():
    s = FakeStorageAdapter()
    await s.upload("k", b"x", "text/plain")
    await s.delete("k")
    assert "k" not in s.objects
