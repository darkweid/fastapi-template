from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
import hashlib

from botocore.exceptions import ClientError
from starlette.datastructures import UploadFile

from src.core.errors.exceptions import InstanceProcessingException
from src.core.storage.s3.interface import StoredObject


class InMemoryS3Client:
    def __init__(self, default_bucket: str = "test-bucket") -> None:
        self._default_bucket = default_bucket
        self._buckets: dict[str, dict[str, bytes]] = {}
        self._modified: dict[str, dict[str, datetime]] = {}
        self.closed = False

    def _get_modified(self, bucket: str | None) -> dict[str, datetime]:
        return self._modified.setdefault(bucket or self._default_bucket, {})

    def _stored(self, key: str, bucket: str | None) -> StoredObject:
        data = self._get_bucket(bucket)[key]
        return StoredObject(
            key=key,
            size=len(data),
            etag=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            last_modified=self._get_modified(bucket)[key],
        )

    def set_last_modified(
        self, key: str, value: datetime, *, bucket: str | None = None
    ) -> None:
        if value.tzinfo is None:
            raise ValueError("last_modified must be timezone-aware.")
        if key not in self._get_bucket(bucket):
            raise FileNotFoundError(f"Object not found: {key}")
        self._get_modified(bucket)[key] = value

    def _get_bucket(self, bucket: str | None) -> dict[str, bytes]:
        name = bucket or self._default_bucket
        if name not in self._buckets:
            self._buckets[name] = {}
        return self._buckets[name]

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        target = self._get_bucket(bucket)
        target[key] = data
        self._get_modified(bucket)[key] = datetime.now(UTC)

    async def upload_uploadfile(
        self,
        key: str,
        file: UploadFile,
        *,
        bucket: str | None = None,
    ) -> None:
        data = await file.read()
        await self.upload_bytes(key, data, bucket=bucket)

    async def upload_large_uploadfile(
        self,
        key: str,
        file: UploadFile,
        *,
        bucket: str | None = None,
        part_size_bytes: int = 8 * 1024 * 1024,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        data = await file.read()
        await self.upload_bytes(
            key,
            data,
            bucket=bucket,
            content_type=content_type,
            cache_control=cache_control,
        )

    async def download_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        source = self._get_bucket(bucket)
        if key not in source:
            raise FileNotFoundError(f"Object not found: {key}")
        return source[key]

    async def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        source = self._get_bucket(bucket)
        source.pop(key, None)
        self._get_modified(bucket).pop(key, None)

    async def list_keys(
        self,
        *,
        prefix: str | None = None,
        bucket: str | None = None,
        max_keys: int | None = None,
    ) -> list[str]:
        source = self._get_bucket(bucket)
        keys = list(source.keys())
        if prefix:
            keys = [key for key in keys if key.startswith(prefix)]
        if max_keys is not None:
            keys = keys[:max_keys]
        return keys

    async def generate_presigned_get_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
    ) -> str:
        name = bucket or self._default_bucket
        ttl = expires_in or 0
        return f"https://s3.local/{name}/{key}?op=get&expires_in={ttl}"

    async def generate_presigned_put_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> str:
        name = bucket or self._default_bucket
        ttl = expires_in or 0
        return f"https://s3.local/{name}/{key}?op=put&expires_in={ttl}"

    async def object_exists(self, key: str, *, bucket: str | None = None) -> bool:
        source = self._get_bucket(bucket)
        return key in source

    async def list_objects(
        self, *, prefix: str, bucket: str | None = None, page_size: int = 1000
    ) -> AsyncIterator[StoredObject]:
        for key in sorted(self._get_bucket(bucket)):
            if key.startswith(prefix):
                yield self._stored(key, bucket)

    async def head_object(
        self, key: str, *, bucket: str | None = None
    ) -> StoredObject | None:
        if key not in self._get_bucket(bucket):
            return None
        return self._stored(key, bucket)

    async def copy_object(
        self, source_key: str, destination_key: str, *, bucket: str | None = None
    ) -> None:
        if source_key == destination_key:
            raise InstanceProcessingException(
                "S3 copy source and destination must differ."
            )
        source = self._get_bucket(bucket)
        if source_key not in source:
            raise ClientError(
                {
                    "Error": {
                        "Code": "NoSuchKey",
                        "Message": f"Object not found: {source_key}",
                    }
                },
                "CopyObject",
            )
        source[destination_key] = source[source_key]
        self._get_modified(bucket)[destination_key] = datetime.now(UTC)

    async def close(self) -> None:
        self.closed = True
