"""MinIO/S3 StorageAdapter via boto3 (S3-compatible).

Runs blocking boto3 calls in a thread (asyncio.to_thread) — boto3 has no
native async. endpoint_url points at the MinIO container; for AWS S3 omit
endpoint_ref in tenant.yaml and the SDK uses the default endpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import boto3

from ai_sdr.schemas.tenant_yaml import StorageConfig
from ai_sdr.storage.base import StorageAdapter
from ai_sdr.storage.factory import register_storage_provider


class MinioStorageAdapter(StorageAdapter):
    def __init__(self, cfg: StorageConfig, secrets: Mapping[str, str]) -> None:
        def _sec(ref: str | None) -> str | None:
            return secrets[ref.removeprefix("secrets/")] if ref else None

        self._bucket = cfg.bucket
        self._endpoint = _sec(cfg.endpoint_ref)
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=_sec(cfg.access_key_ref),
            aws_secret_access_key=_sec(cfg.secret_key_ref),
        )

    async def upload(self, key: str, data: bytes, content_type: str) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return await self.get_url(key)

    async def get_url(self, key: str, expires_in: int = 3600) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=key
        )


@register_storage_provider("minio")
def _build_minio(cfg: StorageConfig, secrets: Mapping[str, str]) -> StorageAdapter:
    return MinioStorageAdapter(cfg, secrets)
