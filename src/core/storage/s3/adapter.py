from __future__ import annotations

import asyncio
from collections.abc import Iterable
from types import TracebackType
from typing import Any, Self

import aioboto3
from botocore.exceptions import ClientError
from starlette.datastructures import UploadFile

from src.core.storage.s3.interface import S3ClientProtocol


class S3Adapter(S3ClientProtocol):
    """
    Async S3 adapter over aioboto3.

    Usage:
    async with S3Adapter(...) as s3:
        await s3.upload_bytes("key", b"data")
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        default_presign_ttl: int,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._default_presign_ttl = default_presign_ttl
        self._session = aioboto3.Session()
        self._client_cm: Any = None
        self._client: Any = None

    async def __aenter__(self) -> Self:
        self._client_cm = self._session.client(
            "s3",
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )
        self._client = await self._client_cm.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close(exc_type, exc, tb)

    def _get_bucket(self, bucket: str | None) -> str:
        return bucket or self._bucket

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError(
                "S3 client is not initialized. Use 'async with S3Adapter(...):'."
            )
        return self._client

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> None:
        client = self._ensure_client()
        put_kwargs: dict[str, Any] = {
            "Bucket": self._get_bucket(bucket),
            "Key": key,
            "Body": data,
        }
        if content_type:
            put_kwargs["ContentType"] = content_type
        await client.put_object(**put_kwargs)

    async def upload_uploadfile(
        self,
        key: str,
        file: UploadFile,
        *,
        bucket: str | None = None,
    ) -> None:
        """Upload a FastAPI UploadFile directly."""
        content = await file.read()
        await self.upload_bytes(
            key=key,
            data=content,
            bucket=bucket,
            content_type=file.content_type or None,
        )

    async def download_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        client = self._ensure_client()
        response = await client.get_object(Bucket=self._get_bucket(bucket), Key=key)
        body = response["Body"]
        data: bytes = await body.read()
        return data

    async def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        client = self._ensure_client()
        await client.delete_object(Bucket=self._get_bucket(bucket), Key=key)

    async def list_keys(
        self,
        *,
        prefix: str | None = None,
        bucket: str | None = None,
        max_keys: int | None = None,
    ) -> list[str]:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {"Bucket": self._get_bucket(bucket)}
        if prefix:
            kwargs["Prefix"] = prefix
        if max_keys:
            kwargs["MaxKeys"] = max_keys

        keys: list[str] = []
        continuation_token: str | None = None

        while True:
            page_kwargs = dict(kwargs)
            if continuation_token:
                page_kwargs["ContinuationToken"] = continuation_token

            response = await client.list_objects_v2(**page_kwargs)
            contents: Iterable[dict[str, Any]] = response.get("Contents", []) or []
            keys.extend(item["Key"] for item in contents if "Key" in item)

            if response.get("IsTruncated"):
                continuation_token = response.get("NextContinuationToken")
                if max_keys and len(keys) >= max_keys:
                    return keys[:max_keys]
                continue
            break

        return keys

    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
        method: str = "get_object",
    ) -> str:
        client = self._ensure_client()
        params = {"Bucket": self._get_bucket(bucket), "Key": key}
        ttl = expires_in or self._default_presign_ttl

        url_or_coro = client.generate_presigned_url(
            ClientMethod=method,
            Params=params,
            ExpiresIn=ttl,
        )
        if asyncio.iscoroutine(url_or_coro):
            return str(await url_or_coro)
        return str(url_or_coro)

    async def object_exists(self, key: str, *, bucket: str | None = None) -> bool:
        client = self._ensure_client()
        try:
            await client.head_object(Bucket=self._get_bucket(bucket), Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise

    async def close(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        tb: TracebackType | None = None,
    ) -> None:
        if self._client_cm:
            await self._client_cm.__aexit__(exc_type, exc, tb)
        self._client = None
        self._client_cm = None
