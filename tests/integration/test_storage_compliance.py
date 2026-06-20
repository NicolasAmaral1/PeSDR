"""Same contract against every StorageAdapter impl."""

from __future__ import annotations

import pytest

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.fake import FakeStorageAdapter
from ai_sdr.storage.minio import MinioStorageAdapter

pytestmark = pytest.mark.integration


class _StubS3:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType):  # noqa: N803
        self.store[f"{Bucket}/{Key}"] = Body

    def generate_presigned_url(self, op, *, Params, ExpiresIn):  # noqa: N803
        return f"https://minio.local/{Params['Bucket']}/{Params['Key']}"

    def delete_object(self, *, Bucket, Key):  # noqa: N803
        self.store.pop(f"{Bucket}/{Key}", None)


@pytest.fixture(params=["fake", "minio"])
def storage_under_test(request) -> StorageAdapter:
    if request.param == "fake":
        return FakeStorageAdapter()
    cfg = StorageConfig(provider="minio", bucket="b", endpoint_ref="secrets/ep")
    a = MinioStorageAdapter.__new__(MinioStorageAdapter)
    a._bucket = "b"
    a._endpoint = "https://minio.local"
    a._client = _StubS3()
    return a


async def test_upload_returns_nonempty_url(storage_under_test):
    url = await storage_under_test.upload("outbound/1.ogg", b"\x01\x02", "audio/ogg")
    assert isinstance(url, str) and url


async def test_delete_is_idempotent(storage_under_test):
    await storage_under_test.delete("missing-key")  # must not raise
