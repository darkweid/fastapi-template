from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

from src.core.storage.s3.adapter import S3Adapter  # noqa: E402


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

    aws_settings = SimpleNamespace(
        BUCKET_NAME="bucket",
        REGION_NAME="us-east-1",
        AWS_ACCESS_KEY_ID="ak",
        AWS_SECRET_ACCESS_KEY="sk",
        PRE_SIGNED_URL_SECONDS=123,
    )
    settings = SimpleNamespace(aws=aws_settings)

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
    client.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "a"}, {"Key": "b"}],
            "IsTruncated": True,
            "NextContinuationToken": "t1",
        },
        {"Contents": [{"Key": "c"}], "IsTruncated": False},
        {"Contents": []},
    ]

    async with adapter:
        keys = await adapter.list_keys(prefix="pfx", max_keys=10)
        empty = await adapter.list_keys()

    assert keys == ["a", "b", "c"]
    assert empty == []
    assert client.list_objects_v2.await_count == 3


@pytest.mark.asyncio
async def test_generate_presigned_url_uses_override(
    s3_mocks: tuple[S3Adapter, AsyncMock, FakeClientCM],
) -> None:
    adapter, client, _ = s3_mocks
    client.generate_presigned_url.return_value = "http://presigned"

    async with adapter:
        url = await adapter.generate_presigned_url("key2", expires_in=123)

    assert url == "http://presigned"
    client.generate_presigned_url.assert_called_once()
    _, kwargs = client.generate_presigned_url.call_args
    assert kwargs["ExpiresIn"] == 123


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
        filename="file.txt", file=AsyncMock(), headers={"content-type": "text/plain"}
    )
    upload.read = AsyncMock(return_value=b"hello")

    async with adapter:
        await adapter.upload_uploadfile("key-upload", upload)

    client.put_object.assert_awaited_once_with(
        Bucket="default-bucket",
        Key="key-upload",
        Body=b"hello",
        ContentType="text/plain",
    )
