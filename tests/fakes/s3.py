from __future__ import annotations

from starlette.datastructures import UploadFile


class InMemoryS3Client:
    def __init__(self, default_bucket: str = "test-bucket") -> None:
        self._default_bucket = default_bucket
        self._buckets: dict[str, dict[str, bytes]] = {}
        self.closed = False

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
    ) -> None:
        target = self._get_bucket(bucket)
        target[key] = data

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
    ) -> None:
        data = await file.read()
        await self.upload_bytes(key, data, bucket=bucket, content_type=content_type)

    async def download_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        source = self._get_bucket(bucket)
        if key not in source:
            raise FileNotFoundError(f"Object not found: {key}")
        return source[key]

    async def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        source = self._get_bucket(bucket)
        source.pop(key, None)

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

    async def close(self) -> None:
        self.closed = True
