from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import inspect
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.datastructures import UploadFile

try:
    import aioboto3  # noqa: F401
    from botocore.exceptions import ClientError
except ImportError:
    pytest.skip("aioboto3/botocore not installed", allow_module_level=True)

from src.core.errors.exceptions import (
    InfrastructureException,
    InstanceProcessingException,
    PayloadTooLargeException,
)
from src.core.storage.s3 import adapter as adapter_module
from src.core.storage.s3.adapter import (
    MAX_MULTIPART_PARTS,
    S3Adapter,
)
from src.core.storage.s3.interface import (
    DEFAULT_MULTIPART_PART_SIZE_BYTES,
    S3ClientProtocol,
    StoredObject,
)
from tests.fakes.s3 import InMemoryS3Client
from tests.helpers.logs import capture_logger

NO_LIMIT = 2**40


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
async def test_build_s3_adapter_constructs_client_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_s3_adapter maps config fields onto the aioboto3 client kwargs.

    Entering/exiting the returned adapter, and get_s3_adapter's disabled/enabled
    branches, are covered separately (test_s3_dependencies.py, test_lifespan.py) -
    the adapter is now built once in lifespan and reused, not per-request.
    """
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)

    from src.core.storage.s3 import dependencies

    s3_settings = SimpleNamespace(
        S3_ENABLED=True,
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

    adapter = dependencies.build_s3_adapter(s3_settings)
    async with adapter:
        assert cm.entered

    assert cm.exited
    assert len(session.client_calls) == 1
    call = session.client_calls[0]
    assert call["args"] == ("s3",)
    assert call["kwargs"]["region_name"] == "us-east-1"
    assert call["kwargs"]["aws_access_key_id"] == "ak"
    assert call["kwargs"]["aws_secret_access_key"] == "sk"
    assert call["kwargs"]["verify"] is True
    assert "config" in call["kwargs"]


def test_build_s3_adapter_raises_when_credentials_missing() -> None:
    from src.core.storage.s3 import dependencies

    s3_settings = SimpleNamespace(
        S3_ENABLED=True,
        S3_BUCKET_NAME=None,
        S3_REGION_NAME="us-east-1",
        S3_ACCESS_KEY_ID="ak",
        S3_SECRET_ACCESS_KEY="sk",
    )

    with pytest.raises(
        InfrastructureException, match="S3 credentials are not configured"
    ):
        dependencies.build_s3_adapter(s3_settings)


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
async def test_upload_bytes_stores_cache_control(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """S3 serves the stored value back as the Cache-Control header of every GET;
    omitting it leaves browsers revalidating each object on every view."""
    adapter, client, _ = s3_mocks

    async with adapter:
        await adapter.upload_bytes("key1", b"hello", cache_control="public, max-age=60")

    assert client.put_object.await_args.kwargs["CacheControl"] == "public, max-age=60"


@pytest.mark.asyncio
async def test_upload_large_uploadfile_stores_cache_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    cm = FakeClientCM(client)
    session = FakeSession(client, cm)
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    monkeypatch.setattr("src.core.storage.s3.adapter.MIN_MULTIPART_PART_SIZE_BYTES", 5)
    monkeypatch.setattr(
        S3Adapter, "_round_up_to_megabyte", lambda self, size_bytes: size_bytes
    )
    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.return_value = {"ETag": "e1"}
    upload = UploadFile(filename="file.txt", file=io.BytesIO(b"01234"))

    async with adapter:
        await adapter.upload_large_uploadfile(
            "key-large",
            upload,
            max_size_bytes=NO_LIMIT,
            part_size_bytes=5,
            cache_control="no-cache",
        )

    create_kwargs = client.create_multipart_upload.await_args.kwargs
    assert create_kwargs["CacheControl"] == "no-cache"


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
        {
            "ContentLength": 1,
            "ETag": '"e"',
            "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
        },
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
            "key-large",
            upload,
            max_size_bytes=NO_LIMIT,
            part_size_bytes=5,
            content_type="text/plain",
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
        await adapter.upload_large_uploadfile(
            "key-large", upload, max_size_bytes=NO_LIMIT, part_size_bytes=5
        )

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
                "key-large", upload, max_size_bytes=NO_LIMIT, part_size_bytes=5
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
                "key-large", upload, max_size_bytes=NO_LIMIT, part_size_bytes=5
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
                "key-large", upload, max_size_bytes=NO_LIMIT, part_size_bytes=1
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
                "key-large", upload, max_size_bytes=NO_LIMIT, part_size_bytes=5
            )

    client.abort_multipart_upload.assert_awaited_once()


@pytest.fixture
def multipart(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[S3Adapter, AsyncMock, FakeClientCM]:
    """The adapter with five-byte parts and an upload already created."""
    adapter, client, cm = s3_mocks
    monkeypatch.setattr(adapter_module, "MIN_MULTIPART_PART_SIZE_BYTES", 5)
    monkeypatch.setattr(
        S3Adapter, "_round_up_to_megabyte", lambda self, size_bytes: size_bytes
    )
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.return_value = {"ETag": "e1"}
    return adapter, client, cm


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="file.txt", file=io.BytesIO(data))


async def _cancel_during_a_part(
    adapter: S3Adapter, client: AsyncMock
) -> asyncio.Task[None]:
    """A multipart upload cancelled while its first part is on the wire."""
    sending = asyncio.Event()

    async def hang(**_: object) -> None:
        sending.set()
        await asyncio.Event().wait()

    client.upload_part.side_effect = hang
    task = asyncio.create_task(
        adapter.upload_large_uploadfile(
            "key-large", _upload(b"012345"), max_size_bytes=NO_LIMIT, part_size_bytes=5
        )
    )
    await asyncio.wait_for(sending.wait(), timeout=10)
    task.cancel()
    return task


async def test_a_cancelled_multipart_upload_is_aborted(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """A client that disconnects mid-upload cancels the request; parts left behind
    are billed until somebody finds them."""
    adapter, client, _ = multipart

    async with adapter:
        task = await _cancel_during_a_part(adapter, client)
        with pytest.raises(asyncio.CancelledError):
            await task

    client.abort_multipart_upload.assert_awaited_once_with(
        Bucket="default-bucket", Key="key-large", UploadId="u1"
    )


async def test_a_repeated_cancellation_does_not_stop_the_abort(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """A cancel scope cancels again at every await, so the abort must survive its
    caller, and the client must not close under it."""
    adapter, client, cm = multipart
    aborting, proceed = asyncio.Event(), asyncio.Event()
    finished: list[bool] = []

    async def slow_abort(**_: object) -> None:
        aborting.set()
        await proceed.wait()
        finished.append(not cm.exited)

    client.abort_multipart_upload.side_effect = slow_abort
    async with adapter:
        task = await _cancel_during_a_part(adapter, client)
        await asyncio.wait_for(aborting.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished == []
        asyncio.get_running_loop().call_soon(proceed.set)

    assert finished == [True]
    assert cm.exited


async def test_a_close_cancelled_while_it_waits_for_an_abort_still_closes_the_client(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, cm = multipart
    aborting, closing = asyncio.Event(), asyncio.Event()

    async def hang(**_: object) -> None:
        aborting.set()
        await asyncio.Event().wait()

    client.abort_multipart_upload.side_effect = hang
    await adapter.__aenter__()
    upload = await _cancel_during_a_part(adapter, client)
    await asyncio.wait_for(aborting.wait(), timeout=10)
    upload.cancel()
    with pytest.raises(asyncio.CancelledError):
        await upload
    (abort,) = adapter._aborts  # noqa: SLF001

    close = asyncio.create_task(adapter.close())
    asyncio.get_running_loop().call_soon(closing.set)
    await closing.wait()
    assert not cm.exited
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close

    assert cm.exited
    abort.cancel()


async def test_an_abort_that_hangs_is_given_up(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The process is killed at the end of stop_grace_period; what waits for the
    abort must get on with its own cleanup before."""
    adapter, client, _ = multipart
    monkeypatch.setattr(adapter_module, "ABORT_TIMEOUT_SECONDS", 0)

    async def hang(**_: object) -> None:
        await asyncio.Event().wait()

    client.abort_multipart_upload.side_effect = hang

    with capture_logger(caplog, adapter_module.logger):
        # Called directly: close() would wait for a hung abort past any timeout here.
        await asyncio.wait_for(
            adapter._abort_multipart_upload(client, UploadId="u1"),  # noqa: SLF001
            timeout=10,
        )

    assert "u1 not aborted" in caplog.text


def test_the_abort_fits_in_the_drain_of_a_stopping_container() -> None:
    assert adapter_module.ABORT_TIMEOUT_SECONDS <= 10


async def test_a_body_declared_empty_is_streamed(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """Stored as an empty object, the content would be lost without an error."""
    adapter, client, _ = multipart
    upload = _upload(b"0123456")
    upload.size = 0

    async with adapter:
        await adapter.upload_large_uploadfile(
            "key-large", upload, max_size_bytes=7, part_size_bytes=5
        )

    client.put_object.assert_not_awaited()
    sent = [call.kwargs["Body"] for call in client.upload_part.await_args_list]
    assert sent == [b"01234", b"56"]


async def test_a_body_declared_empty_is_held_to_the_limit(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = multipart
    upload = _upload(b"0123456")
    upload.size = 0

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, max_size_bytes=6, part_size_bytes=5
            )

    client.complete_multipart_upload.assert_not_awaited()


async def test_an_empty_body_is_stored_as_an_empty_object(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """A multipart upload without a part cannot be completed."""
    adapter, client, _ = multipart

    async with adapter:
        await adapter.upload_large_uploadfile(
            "key-large", _upload(b""), max_size_bytes=0, part_size_bytes=5
        )

    assert client.put_object.await_args.kwargs["Body"] == b""
    client.create_multipart_upload.assert_not_awaited()


async def test_a_failed_abort_does_not_replace_the_upload_error(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, client, _ = multipart
    client.upload_part.side_effect = RuntimeError("part refused")
    client.abort_multipart_upload.side_effect = ConnectionError("abort refused")

    with capture_logger(caplog, adapter_module.logger):
        async with adapter:
            with pytest.raises(RuntimeError, match="part refused") as caught:
                await adapter.upload_large_uploadfile(
                    "key-large",
                    _upload(b"012345"),
                    max_size_bytes=NO_LIMIT,
                    part_size_bytes=5,
                )

    assert caught.value.__context__ is None
    assert "u1 not aborted" in caplog.text


async def test_a_declared_size_over_the_limit_creates_no_upload(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = multipart
    upload = _upload(b"0")
    upload.size = 12

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, max_size_bytes=11, part_size_bytes=5
            )

    client.create_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize(
    ("limit", "parts_sent"), [(4, 0), (11, 2)], ids=["first part", "later part"]
)
async def test_a_body_larger_than_it_declared_is_refused_while_streaming(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM], limit: int, parts_sent: int
) -> None:
    """The declared size comes from the client, so it cannot be the only check."""
    adapter, client, _ = multipart
    upload = _upload(b"0123456789AB")
    upload.size = 1

    async with adapter:
        with pytest.raises(PayloadTooLargeException):
            await adapter.upload_large_uploadfile(
                "key-large", upload, max_size_bytes=limit, part_size_bytes=5
            )

    assert client.upload_part.await_count == parts_sent
    client.abort_multipart_upload.assert_awaited_once()
    client.complete_multipart_upload.assert_not_awaited()


async def test_a_body_of_exactly_the_limit_is_stored(
    multipart: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = multipart

    async with adapter:
        await adapter.upload_large_uploadfile(
            "key-large", _upload(b"0123456789A"), max_size_bytes=11, part_size_bytes=5
        )

    client.complete_multipart_upload.assert_awaited_once()


@pytest.mark.parametrize("client_type", [S3ClientProtocol, S3Adapter, InMemoryS3Client])
def test_every_client_splits_by_the_same_part_size(client_type: type) -> None:
    parameters = inspect.signature(client_type.upload_large_uploadfile).parameters

    assert parameters["part_size_bytes"].default == DEFAULT_MULTIPART_PART_SIZE_BYTES


async def test_a_client_that_fails_to_close_does_not_fail_the_finished_work(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A task whose send already went out would be retried and send again."""
    adapter, client, _ = s3_mocks
    monkeypatch.setattr(FakeClientCM, "__aexit__", _refuse)

    with capture_logger(caplog, adapter_module.logger):
        async with adapter:
            await adapter.upload_bytes("key", b"data")

    client.put_object.assert_awaited_once()
    assert "client failed to close" in caplog.text


async def test_a_client_that_fails_to_close_keeps_the_error_of_the_block(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _, _ = s3_mocks
    monkeypatch.setattr(FakeClientCM, "__aexit__", _refuse)

    with pytest.raises(ValueError, match="block"):
        async with adapter:
            raise ValueError("block")


async def _refuse(*_: object) -> None:
    raise ConnectionError("pool refused to close")


@pytest.mark.asyncio
async def test_list_objects_yields_metadata_page_by_page(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    paginator = FakePaginator(
        [
            {
                "Contents": [
                    {"Key": "p/a", "Size": 3, "ETag": '"e1"', "LastModified": stamp}
                ]
            },
            {
                "Contents": [
                    {"Key": "p/b", "Size": 5, "ETag": '"e2"', "LastModified": stamp}
                ]
            },
            {},
        ]
    )
    client.get_paginator = Mock(return_value=paginator)

    async with adapter:
        objects = [
            item async for item in adapter.list_objects(prefix="p/", page_size=2)
        ]

    assert objects == [
        StoredObject(key="p/a", size=3, etag="e1", last_modified=stamp),
        StoredObject(key="p/b", size=5, etag="e2", last_modified=stamp),
    ]
    assert paginator.paginate_calls == [
        {
            "Bucket": "default-bucket",
            "Prefix": "p/",
            "PaginationConfig": {"PageSize": 2},
        }
    ]


@pytest.mark.asyncio
async def test_list_objects_survives_empty_and_missing_contents(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """S3 omits `Contents` on an empty listing and a page may carry None."""
    adapter, client, _ = s3_mocks
    client.get_paginator = Mock(
        return_value=FakePaginator([{}, {"Contents": None}, {"Contents": []}])
    )

    async with adapter:
        objects = [item async for item in adapter.list_objects(prefix="none/")]

    assert objects == []


@pytest.mark.asyncio
async def test_list_objects_honours_bucket_override_and_default_page_size(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    paginator = FakePaginator([])
    client.get_paginator = Mock(return_value=paginator)

    async with adapter:
        _ = [item async for item in adapter.list_objects(prefix="p/", bucket="other")]

    assert paginator.paginate_calls == [
        {"Bucket": "other", "Prefix": "p/", "PaginationConfig": {"PageSize": 1000}}
    ]


@pytest.mark.asyncio
async def test_list_objects_keeps_unquoted_and_inner_quote_free_etag(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """A multipart ETag (`hash-3`) and an unquoted one both come out unwrapped."""
    adapter, client, _ = s3_mocks
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    client.get_paginator = Mock(
        return_value=FakePaginator(
            [
                {
                    "Contents": [
                        {
                            "Key": "a",
                            "Size": 1,
                            "ETag": '"abc-3"',
                            "LastModified": stamp,
                        },
                        {"Key": "b", "Size": 1, "ETag": "plain", "LastModified": stamp},
                    ]
                }
            ]
        )
    )

    async with adapter:
        etags = [item.etag async for item in adapter.list_objects(prefix="")]

    assert etags == ["abc-3", "plain"]


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [0, -1, 1001])
async def test_list_objects_refuses_page_size_outside_bounds(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM], page_size: int
) -> None:
    adapter, client, _ = s3_mocks
    client.get_paginator = Mock()

    async with adapter:
        with pytest.raises(ValueError):
            _ = [
                item
                async for item in adapter.list_objects(prefix="p/", page_size=page_size)
            ]

    client.get_paginator.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [1, 1000])
async def test_list_objects_accepts_page_size_bounds(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM], page_size: int
) -> None:
    adapter, client, _ = s3_mocks
    paginator = FakePaginator([])
    client.get_paginator = Mock(return_value=paginator)

    async with adapter:
        _ = [
            item
            async for item in adapter.list_objects(prefix="p/", page_size=page_size)
        ]

    assert paginator.paginate_calls[0]["PaginationConfig"] == {"PageSize": page_size}


@pytest.mark.asyncio
async def test_head_object_returns_metadata_or_none(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    client.head_object.side_effect = [
        {"ContentLength": 7, "ETag": '"abc"', "LastModified": stamp},
        ClientError({"Error": {"Code": "404"}}, "HeadObject"),
    ]

    async with adapter:
        found = await adapter.head_object("k1")
        missing = await adapter.head_object("k2")

    assert found == StoredObject(key="k1", size=7, etag="abc", last_modified=stamp)
    assert missing is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
async def test_head_object_treats_every_missing_code_as_none(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM], code: str
) -> None:
    adapter, client, _ = s3_mocks
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": code}}, "HeadObject"
    )

    async with adapter:
        assert await adapter.head_object("k") is None


@pytest.mark.asyncio
async def test_head_object_raises_on_other_errors(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "500"}}, "HeadObject"
    )

    async with adapter:
        with pytest.raises(ClientError):
            await adapter.head_object("k1")


@pytest.mark.asyncio
async def test_head_object_raises_on_access_denied_by_default(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """Hiding a permissions problem as 'missing' would make a cleanup job
    believe objects are gone; it is opt-in."""
    adapter, client, _ = s3_mocks
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "403"}}, "HeadObject"
    )

    async with adapter:
        with pytest.raises(ClientError):
            await adapter.head_object("k1")


@pytest.mark.asyncio
async def test_head_object_access_denied_is_none_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    session = FakeSession(client, FakeClientCM(client))
    monkeypatch.setattr("src.core.storage.s3.adapter.aioboto3.Session", lambda: session)
    adapter = S3Adapter(
        bucket="default-bucket",
        region="us-east-1",
        access_key="ak",
        secret_key="sk",
        default_presign_ttl=300,
        treat_access_denied_as_missing=True,
    )
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "403"}}, "HeadObject"
    )

    async with adapter:
        assert await adapter.head_object("k1") is None


@pytest.mark.asyncio
async def test_copy_object_copies_server_side_with_metadata(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks

    async with adapter:
        await adapter.copy_object("a/src", "trash/a/src")

    client.copy_object.assert_awaited_once_with(
        Bucket="default-bucket",
        Key="trash/a/src",
        CopySource={"Bucket": "default-bucket", "Key": "a/src"},
        MetadataDirective="COPY",
    )


@pytest.mark.asyncio
async def test_copy_object_uses_bucket_override_for_both_sides(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks

    async with adapter:
        await adapter.copy_object("a", "b", bucket="other")

    client.copy_object.assert_awaited_once_with(
        Bucket="other",
        Key="b",
        CopySource={"Bucket": "other", "Key": "a"},
        MetadataDirective="COPY",
    )


@pytest.mark.asyncio
async def test_copy_object_propagates_missing_source(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """Unlike HEAD, a copy has no 'missing' answer: the caller must not move
    on to delete the original after a copy that did not happen."""
    adapter, client, _ = s3_mocks
    client.copy_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "CopyObject"
    )

    async with adapter:
        with pytest.raises(ClientError):
            await adapter.copy_object("gone", "trash/gone")


@pytest.mark.asyncio
async def test_copy_object_onto_itself_is_refused_before_the_call(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    """S3 answers InvalidRequest for a self-copy with unchanged metadata."""
    adapter, client, _ = s3_mocks

    async with adapter:
        with pytest.raises(InstanceProcessingException):
            await adapter.copy_object("same", "same")

    client.copy_object.assert_not_awaited()
