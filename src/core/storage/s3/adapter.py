from __future__ import annotations

import asyncio
import math
from types import TracebackType
from typing import Any, Self

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError
from starlette.datastructures import UploadFile

from src.core.errors.exceptions import (
    InfrastructureException,
    InstanceProcessingException,
    PayloadTooLargeException,
)
from src.core.storage.s3.interface import S3ClientProtocol

MIN_MULTIPART_PART_SIZE_BYTES = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000


class S3Adapter(S3ClientProtocol):
    """
    Async S3 adapter over aioboto3.

    Usage:
    async with S3Adapter(...) as s3:
        await s3.upload_bytes("key", b"data")

    Note:
    - Intended for small objects. For large files use streaming/multipart uploads.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        default_presign_ttl: int,
        endpoint_url: str | None = None,
        addressing_style: str | None = None,
        signature_version: str = "s3v4",
        verify_ssl: bool = True,
        ca_bundle: str | None = None,
        treat_access_denied_as_missing: bool = False,
        connect_timeout_seconds: int = 5,
        read_timeout_seconds: int = 60,
        retry_max_attempts: int = 3,
        retry_mode: str = "standard",
        max_upload_size_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._default_presign_ttl = default_presign_ttl
        self._endpoint_url = endpoint_url
        self._verify_ssl = verify_ssl
        self._ca_bundle = ca_bundle
        self._treat_access_denied_as_missing = treat_access_denied_as_missing
        self._max_upload_size_bytes = max_upload_size_bytes
        self._session = aioboto3.Session()
        self._client_cm: Any = None
        self._client: Any = None
        self._client_config = self._build_client_config(
            signature_version=signature_version,
            addressing_style=addressing_style,
            endpoint_url=endpoint_url,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            retry_max_attempts=retry_max_attempts,
            retry_mode=retry_mode,
        )

    async def __aenter__(self) -> Self:
        client_kwargs: dict[str, Any] = {
            "region_name": self._region,
            "aws_access_key_id": self._access_key,
            "aws_secret_access_key": self._secret_key,
        }
        if self._endpoint_url:
            client_kwargs["endpoint_url"] = self._endpoint_url
        if self._client_config:
            client_kwargs["config"] = self._client_config
        if self._ca_bundle:
            client_kwargs["verify"] = self._ca_bundle
        else:
            client_kwargs["verify"] = self._verify_ssl
        self._client_cm = self._session.client("s3", **client_kwargs)
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

    def _build_client_config(
        self,
        *,
        signature_version: str,
        addressing_style: str | None,
        endpoint_url: str | None,
        connect_timeout_seconds: int,
        read_timeout_seconds: int,
        retry_max_attempts: int,
        retry_mode: str,
    ) -> Config | None:
        config_kwargs: dict[str, Any] = {}
        config_kwargs["signature_version"] = signature_version
        resolved_addressing_style = self._resolve_addressing_style(
            addressing_style=addressing_style,
            endpoint_url=endpoint_url,
        )
        if resolved_addressing_style:
            config_kwargs["s3"] = {"addressing_style": resolved_addressing_style}
        config_kwargs["connect_timeout"] = connect_timeout_seconds
        config_kwargs["read_timeout"] = read_timeout_seconds
        config_kwargs["retries"] = {
            "max_attempts": retry_max_attempts,
            "mode": retry_mode,
        }
        return Config(**config_kwargs)

    def _resolve_addressing_style(
        self,
        *,
        addressing_style: str | None,
        endpoint_url: str | None,
    ) -> str:
        if addressing_style:
            return addressing_style
        if endpoint_url:
            return "path"
        return "auto"

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise InfrastructureException(
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
        """Upload a FastAPI UploadFile directly (small files only)."""
        client = self._ensure_client()
        size = self._get_uploadfile_size(file)
        if size is None:
            data = await self._read_uploadfile_with_limit(file)
            await self.upload_bytes(
                key=key,
                data=data,
                bucket=bucket,
                content_type=file.content_type or None,
            )
            return
        if size > self._max_upload_size_bytes:
            raise PayloadTooLargeException("Upload file size exceeds configured limit.")
        await file.seek(0)
        put_kwargs: dict[str, Any] = {
            "Bucket": self._get_bucket(bucket),
            "Key": key,
            "Body": file.file,
        }
        if file.content_type:
            put_kwargs["ContentType"] = file.content_type
        await client.put_object(**put_kwargs)

    async def upload_large_uploadfile(
        self,
        key: str,
        file: UploadFile,
        *,
        bucket: str | None = None,
        part_size_bytes: int = 16 * 1024 * 1024,
        content_type: str | None = None,
    ) -> None:
        """
        Upload a large UploadFile using multipart upload.

        Note:
        - Part size must be at least 5 MB (except the last part).
        - Size limits are not enforced here; callers must validate large uploads.
        - The UploadFile source should be seekable; synchronous file backends may
          block the event loop under high concurrency.
        """
        if part_size_bytes < MIN_MULTIPART_PART_SIZE_BYTES:
            raise InfrastructureException("Multipart part size must be at least 5 MB.")
        size = self._get_uploadfile_size(file)
        if size is not None:
            if size == 0:
                await self.upload_bytes(
                    key=key,
                    data=b"",
                    bucket=bucket,
                    content_type=content_type or file.content_type or None,
                )
                return
            required_part_size = max(
                MIN_MULTIPART_PART_SIZE_BYTES,
                math.ceil(size / MAX_MULTIPART_PARTS),
            )
            required_part_size = self._round_up_to_megabyte(required_part_size)
            if part_size_bytes < required_part_size:
                part_size_bytes = required_part_size
        client = self._ensure_client()
        await file.seek(0)
        bucket_name = self._get_bucket(bucket)
        resolved_content_type = content_type or file.content_type or None
        create_kwargs: dict[str, Any] = {
            "Bucket": bucket_name,
            "Key": key,
        }
        if resolved_content_type:
            create_kwargs["ContentType"] = resolved_content_type

        response = await client.create_multipart_upload(**create_kwargs)
        upload_id = response["UploadId"]
        parts: list[dict[str, Any]] = []

        try:
            part_number = 1
            first_chunk = await file.read(part_size_bytes)
            if not first_chunk:
                raise InfrastructureException("Empty stream for non-empty file.")
            upload_response = await client.upload_part(
                Bucket=bucket_name,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=first_chunk,
            )
            parts.append({"ETag": upload_response["ETag"], "PartNumber": part_number})
            part_number += 1

            while True:
                chunk = await file.read(part_size_bytes)
                if not chunk:
                    break
                if part_number > MAX_MULTIPART_PARTS:
                    raise PayloadTooLargeException(
                        "Multipart upload exceeds the maximum number of parts."
                    )
                upload_response = await client.upload_part(
                    Bucket=bucket_name,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk,
                )
                parts.append(
                    {"ETag": upload_response["ETag"], "PartNumber": part_number}
                )
                part_number += 1

            await client.complete_multipart_upload(
                Bucket=bucket_name,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            await client.abort_multipart_upload(
                Bucket=bucket_name,
                Key=key,
                UploadId=upload_id,
            )
            raise

    async def download_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        """Download an object into memory (small files only)."""
        client = self._ensure_client()
        response = await client.get_object(Bucket=self._get_bucket(bucket), Key=key)
        body = response["Body"]
        data: bytes = await body.read()
        return data

    def _get_uploadfile_size(self, file: UploadFile) -> int | None:
        size = getattr(file, "size", None)
        if isinstance(size, int):
            return size
        headers = getattr(file, "headers", None)
        if headers:
            header_size = headers.get("content-length")
            if header_size:
                try:
                    return int(header_size)
                except (TypeError, ValueError):
                    pass
        file_obj = file.file
        try:
            current_pos = file_obj.tell()
        except Exception:
            current_pos = None
        try:
            file_obj.seek(0, 2)
            size = file_obj.tell()
        except Exception:
            return None
        finally:
            if current_pos is not None:
                try:
                    file_obj.seek(current_pos)
                except Exception:
                    pass
        return size

    def _round_up_to_megabyte(self, size_bytes: int) -> int:
        megabyte = 1024 * 1024
        return math.ceil(size_bytes / megabyte) * megabyte

    async def _read_uploadfile_with_limit(self, file: UploadFile) -> bytes:
        try:
            await file.seek(0)
        except Exception:
            pass
        max_size = self._max_upload_size_bytes
        chunk_size = min(1024 * 1024, max_size)
        buffer = bytearray()
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > max_size:
                raise PayloadTooLargeException(
                    "Upload file size exceeds configured limit."
                )
        return bytes(buffer)

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
        if max_keys is not None and max_keys <= 0:
            return []

        pagination_config: dict[str, int] = {}
        if max_keys is not None:
            pagination_config["MaxItems"] = max_keys
            pagination_config["PageSize"] = min(max_keys, 1000)

        paginator = client.get_paginator("list_objects_v2")
        paginate_kwargs = dict(kwargs)
        if pagination_config:
            paginate_kwargs["PaginationConfig"] = pagination_config

        keys: list[str] = []
        async for page in paginator.paginate(**paginate_kwargs):
            contents = page.get("Contents") or []
            keys.extend(item["Key"] for item in contents if "Key" in item)
        return keys

    async def generate_presigned_get_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
    ) -> str:
        return await self._generate_presigned_url(
            key=key,
            bucket=bucket,
            expires_in=expires_in,
            method="get_object",
            content_type=None,
        )

    async def generate_presigned_put_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> str:
        return await self._generate_presigned_url(
            key=key,
            bucket=bucket,
            expires_in=expires_in,
            method="put_object",
            content_type=content_type,
        )

    async def _generate_presigned_url(
        self,
        *,
        key: str,
        bucket: str | None,
        expires_in: int | None,
        method: str,
        content_type: str | None,
    ) -> str:
        client = self._ensure_client()
        if "://" in key or key.startswith("http"):
            raise InstanceProcessingException(
                "S3 object key must not be a URL or contain a scheme."
            )
        params = {"Bucket": self._get_bucket(bucket), "Key": key}
        if content_type:
            params["ContentType"] = content_type
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
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {
                "404",
                "NoSuchKey",
                "NotFound",
            }:
                return False
            if self._treat_access_denied_as_missing and error_code in {
                "403",
                "AccessDenied",
                "Forbidden",
            }:
                return False
            raise

    async def close(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        tb: TracebackType | None = None,
    ) -> None:
        client_cm = self._client_cm
        self._client = None
        self._client_cm = None
        if client_cm:
            await client_cm.__aexit__(exc_type, exc, tb)
