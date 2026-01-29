from __future__ import annotations

import io
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.datastructures import UploadFile

try:
    import aioboto3  # noqa: F401
    from botocore.exceptions import ClientError
except ImportError:
    pytest.skip("aioboto3/botocore not installed", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.errors.exceptions import (  # noqa: E402
    InfrastructureException,
    InstanceProcessingException,
    PayloadTooLargeException,
)
from src.core.storage.s3.adapter import (  # noqa: E402
    MAX_MULTIPART_PARTS,
    S3Adapter,
)


class FakeClientCM:
    def __init__(self, client: AsyncMock) -> None:
        self._client = client
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> AsyncMock:
        self.entered = True
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.exited = True


class FakeSession:
    def __init__(self, client: AsyncMock, cm: FakeClientCM) -> None:
        self._client = client
        self._cm = cm
        self.client_calls: list[dict] = []

    def client(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.client_calls.append({"args": args, "kwargs": kwargs})
        return self._cm


class FakePaginator:
    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.paginate_calls: list[dict] = []

    async def paginate(self, **kwargs):  # type: ignore[no-untyped-def]
        self.paginate_calls.append(kwargs)
        for page in self._pages:
            yield page


class NonSeekableFile:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._pos
        end = min(self._pos + size, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk


class SeekableNoTellFile:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._pos
        end = min(self._pos + size, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk

    def seek(self, offset: int, whence: int = 0) -> None:
        if whence == 0:
            self._pos = max(0, min(offset, len(self._data)))
        elif whence == 1:
            self._pos = max(0, min(self._pos + offset, len(self._data)))
        elif whence == 2:
            self._pos = max(0, min(len(self._data) + offset, len(self._data)))
        else:
            raise ValueError("Invalid whence value.")

    def tell(self) -> int:
        raise OSError("tell is not supported")


@pytest.fixture()
def s3_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[S3Adapter, AsyncMock, FakeClientCM]:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    return adapter, client, cm


@pytest.mark.asyncio
async def test_dependency_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    from src.core.storage.s3 import dependencies

    s3_settings = SimpleNamespace(
        S3_BUCKET_NAME="bucket",
        S3_REGION_NAME="us-east-1",
        S3_ACCESS_KEY_ID="ak",
        S3_SECRET_ACCESS_KEY="sk",
        S3_PRE_SIGNED_URL_SECONDS=123,
        S3_ENDPOINT_URL=None,
        S3_ADDRESSING_STYLE="auto",
        S3_SIGNATURE_VERSION="s3v4",
        S3_VERIFY_SSL=True,
        S3_CA_BUNDLE=None,
        S3_TREAT_ACCESS_DENIED_AS_MISSING=False,
        S3_CONNECT_TIMEOUT_SECONDS=5,
        S3_READ_TIMEOUT_SECONDS=60,
        S3_RETRY_MAX_ATTEMPTS=3,
        S3_RETRY_MODE="standard",
        S3_MAX_UPLOAD_SIZE_BYTES=20 * 1024 * 1024,
    )
    settings = SimpleNamespace(s3=s3_settings)

    gen = dependencies.get_s3_adapter(settings)
    adapter = await gen.__anext__()  # noqa: F841
    assert cm.entered
    await gen.aclose()
    assert cm.exited


@pytest.mark.asyncio
async def test_context_manager_initializes_client(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, _, cm = s3_mocks

    async with adapter:
        assert cm.entered
    assert cm.exited


@pytest.mark.asyncio
async def test_upload_and_download_bytes(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.get_object.return_value = {
        "Body": AsyncMock(read=AsyncMock(return_value=b"data"))
    }

    async with adapter:
        await adapter.upload_bytes("key1", b"hello", content_type="text/plain")
        data = await adapter.download_bytes("key1")

    client.put_object.assert_awaited_once_with(
        Bucket="default-bucket", Key="key1", Body=b"hello", ContentType="text/plain"
    )
    client.get_object.assert_awaited_once()
    assert data == b"data"


@pytest.mark.asyncio
async def test_list_keys_returns_keys_or_empty(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    paginator_with_keys = FakePaginator(
        [{"Contents": [{"Key": "a"}, {"Key": "b"}]}, {"Contents": [{"Key": "c"}]}]
    )
    paginator_empty = FakePaginator([{"Contents": []}])
    client.get_paginator = Mock(side_effect=[paginator_with_keys, paginator_empty])

    async with adapter:
        keys = await adapter.list_keys(prefix="pfx", max_keys=10)
        empty = await adapter.list_keys()

    assert keys == ["a", "b", "c"]
    assert empty == []
    assert client.get_paginator.call_count == 2


@pytest.mark.asyncio
async def test_generate_presigned_get_url_uses_override(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.generate_presigned_url = Mock(return_value="http://presigned")

    async with adapter:
        url = await adapter.generate_presigned_get_url("key2", expires_in=123)

    assert url == "http://presigned"
    client.generate_presigned_url.assert_called_once()
    _, kwargs = client.generate_presigned_url.call_args
    assert kwargs["ClientMethod"] == "get_object"
    assert kwargs["ExpiresIn"] == 123


@pytest.mark.asyncio
async def test_generate_presigned_put_url_includes_content_type(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.generate_presigned_url = Mock(return_value="http://presigned")

    async with adapter:
        url = await adapter.generate_presigned_put_url(
            "key2",
            content_type="image/png",
        )

    assert url == "http://presigned"
    _, kwargs = client.generate_presigned_url.call_args
    assert kwargs["ClientMethod"] == "put_object"
    assert kwargs["Params"]["ContentType"] == "image/png"


def test_addressing_style_default_for_endpoint() -> None:
    adapter = S3Adapter(
        bucket="bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=60,
        endpoint_url="https://s3.example.com",
    )
    assert adapter._client_config is not None
    assert adapter._client_config.s3["addressing_style"] == "path"


def test_addressing_style_default_for_aws() -> None:
    adapter = S3Adapter(
        bucket="bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=60,
        endpoint_url=None,
    )
    assert adapter._client_config is not None
    assert adapter._client_config.s3["addressing_style"] == "auto"


@pytest.mark.asyncio
async def test_generate_presigned_get_url_rejects_url_key(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, _, _ = s3_mocks

    async with adapter:
        with pytest.raises(InstanceProcessingException):
            await adapter.generate_presigned_get_url("https://example.com/file")


@pytest.mark.asyncio
async def test_object_exists_true_and_false(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.head_object.side_effect = [
        None,
        ClientError({"Error": {"Code": "404"}}, "HeadObject"),
    ]

    async with adapter:
        exists = await adapter.object_exists("key3")
        missing = await adapter.object_exists("key4")

    assert exists is True
    assert missing is False


@pytest.mark.asyncio
async def test_object_exists_treats_access_denied_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
        treat_access_denied_as_missing=True,
    )
    client.head_object.side_effect = [
        ClientError({"Error": {"Code": "403"}}, "HeadObject"),
    ]

    async with adapter:
        missing = await adapter.object_exists("key4")

    assert missing is False


@pytest.mark.asyncio
async def test_delete_object(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks

    async with adapter:
        await adapter.delete_object("key5")

    client.delete_object.assert_awaited_once_with(Bucket="default-bucket", Key="key5")


@pytest.mark.asyncio
async def test_upload_uploadfile(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"hello"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        await adapter.upload_uploadfile("key-upload", upload)

    client.put_object.assert_awaited_once_with(
        Bucket="default-bucket",
        Key="key-upload",
        Body=upload.file,
        ContentType="text/plain",
    )


@pytest.mark.asyncio
async def test_upload_uploadfile_rejects_large_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
        max_upload_size_bytes=1,
    )
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"hi"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_uploadfile("key-upload", upload)


@pytest.mark.asyncio
async def test_upload_uploadfile_rejects_large_file_without_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
        max_upload_size_bytes=5,
    )
    upload = UploadFile(
        filename="file.txt",
        file=NonSeekableFile(b"0123456789"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_uploadfile("key-upload", upload)


@pytest.mark.asyncio
async def test_upload_large_uploadfile_multipart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    monkeypatch.setattr(
        S3Adapter, "_round_up_to_megabyte", lambda self, size_bytes: size_bytes
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.side_effect = [
        {"ETag": "e1"},
        {"ETag": "e2"},
        {"ETag": "e3"},
    ]
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"0123456789A"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        await adapter.upload_large_uploadfile(
            "key-large", upload, part_size_bytes=5, content_type="text/plain"
        )

    client.create_multipart_upload.assert_awaited_once()
    assert client.upload_part.await_count == 3
    client.complete_multipart_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_large_uploadfile_autoadjusts_part_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.side_effect = [{"ETag": "e1"}]
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"x"),
        headers={"content-type": "text/plain"},
    )
    upload.size = MAX_MULTIPART_PARTS * 6 * 1024 * 1024
    upload.read = AsyncMock(side_effect=[b"x", b""])

    async with adapter:
        await adapter.upload_large_uploadfile("key-large", upload, part_size_bytes=5)

    assert upload.read.call_args_list[0].args[0] == 6 * 1024 * 1024


def test_round_up_to_megabyte() -> None:
    adapter = S3Adapter(
        bucket="bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=60,
    )
    assert adapter._round_up_to_megabyte(5 * 1024 * 1024 + 1) == 6 * 1024 * 1024


@pytest.mark.asyncio
async def test_upload_large_uploadfile_rejects_too_many_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)
    monkeypatch.setattr("src.core.storage.s3.adapter.MAX_MULTIPART_PARTS", 2)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    monkeypatch.setattr(
        S3Adapter, "_round_up_to_megabyte", lambda self, size_bytes: size_bytes
    )
    upload = UploadFile(
        filename="file.txt",
        file=SeekableNoTellFile(b"0123456789ABCDEF"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, part_size_bytes=5
            )


@pytest.mark.asyncio
async def test_upload_large_uploadfile_aborts_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    monkeypatch.setattr(
        S3Adapter, "_round_up_to_megabyte", lambda self, size_bytes: size_bytes
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.side_effect = [
        {"ETag": "e1"},
        RuntimeError("boom"),
    ]
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"0123456789A"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        with pytest.raises(RuntimeError):
            await adapter.upload_large_uploadfile(
                "key-large", upload, part_size_bytes=5
            )

    client.abort_multipart_upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_large_uploadfile_requires_min_part_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b"data"),
        headers={"content-type": "text/plain"},
    )

    async with adapter:
        with pytest.raises(InfrastructureException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, part_size_bytes=1
            )


@pytest.mark.asyncio
async def test_upload_large_uploadfile_empty_stream_raises_infrastructure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)

    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    upload = UploadFile(
        filename="file.txt",
        file=io.BytesIO(b""),
        headers={"content-type": "text/plain"},
    )
    upload.size = 1

    async with adapter:
        with pytest.raises(InfrastructureException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, part_size_bytes=5
            )

    client.abort_multipart_upload.assert_awaited_once()
