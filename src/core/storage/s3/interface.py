from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from starlette.datastructures import UploadFile


@dataclass(frozen=True, slots=True)
class StoredObject:
    """One object as the bucket reports it. `etag` comes without the quotes
    S3 wraps it in, so two reports of one object compare equal."""

    key: str
    size: int
    etag: str
    last_modified: datetime


class S3ClientProtocol(Protocol):
    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None: ...
    async def upload_uploadfile(
        self, key: str, file: UploadFile, *, bucket: str | None = None
    ) -> None: ...
    async def upload_large_uploadfile(
        self,
        key: str,
        file: UploadFile,
        *,
        bucket: str | None = None,
        part_size_bytes: int = 8 * 1024 * 1024,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None: ...
    async def download_bytes(self, key: str, *, bucket: str | None = None) -> bytes: ...
    async def delete_object(self, key: str, *, bucket: str | None = None) -> None: ...
    async def list_keys(
        self,
        *,
        prefix: str | None = None,
        bucket: str | None = None,
        max_keys: int | None = None,
    ) -> list[str]: ...
    def list_objects(
        self, *, prefix: str, bucket: str | None = None, page_size: int = 1000
    ) -> AsyncIterator[StoredObject]: ...
    async def head_object(
        self, key: str, *, bucket: str | None = None
    ) -> StoredObject | None: ...
    async def copy_object(
        self, source_key: str, destination_key: str, *, bucket: str | None = None
    ) -> None: ...
    async def generate_presigned_get_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
    ) -> str: ...
    async def generate_presigned_put_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> str: ...
    async def object_exists(self, key: str, *, bucket: str | None = None) -> bool: ...
    async def close(self) -> None: ...
