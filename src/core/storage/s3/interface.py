from __future__ import annotations

from typing import Protocol

from starlette.datastructures import UploadFile


class S3ClientProtocol(Protocol):
    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
    ) -> None: ...
    async def upload_uploadfile(
        self, key: str, file: UploadFile, *, bucket: str | None = None
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
    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in: int | None = None,
        bucket: str | None = None,
        method: str = "get_object",
    ) -> str: ...
    async def object_exists(self, key: str, *, bucket: str | None = None) -> bool: ...
    async def close(self) -> None: ...
