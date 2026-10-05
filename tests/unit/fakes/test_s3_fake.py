from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib

from botocore.exceptions import ClientError
import pytest

from src.core.errors.exceptions import InstanceProcessingException
from tests.fakes.s3 import InMemoryS3Client


async def test_fake_reports_metadata_copies_and_ages_objects() -> None:
    """Cleanup tests drive the clock through `set_last_modified`; the fake
    must report what a real bucket reports or those tests prove nothing."""
    s3 = InMemoryS3Client()
    await s3.upload_bytes("p/a", b"abc")
    old = datetime.now(UTC) - timedelta(days=10)
    s3.set_last_modified("p/a", old)

    head = await s3.head_object("p/a")
    assert head is not None
    assert head.etag == hashlib.md5(b"abc", usedforsecurity=False).hexdigest()
    assert head.size == 3
    assert head.last_modified == old

    await s3.copy_object("p/a", "trash/p/a")
    copied = await s3.head_object("trash/p/a")
    assert copied is not None and copied.etag == head.etag

    listed = [item.key async for item in s3.list_objects(prefix="p/")]
    assert listed == ["p/a"]
    assert await s3.head_object("p/missing") is None


async def test_fake_copy_gets_a_fresh_timestamp_and_leaves_the_source() -> None:
    """A real copy is a new object; an old source must not make the copy look old."""
    s3 = InMemoryS3Client()
    await s3.upload_bytes("a", b"x")
    old = datetime.now(UTC) - timedelta(days=30)
    s3.set_last_modified("a", old)

    await s3.copy_object("a", "b")

    source = await s3.head_object("a")
    copied = await s3.head_object("b")
    assert source is not None and source.last_modified == old
    assert copied is not None and copied.last_modified > old


async def test_fake_copy_of_missing_source_and_onto_itself_raise() -> None:
    s3 = InMemoryS3Client()
    await s3.upload_bytes("a", b"x")

    with pytest.raises(ClientError) as raised:
        await s3.copy_object("missing", "b")
    assert raised.value.response["Error"]["Code"] == "NoSuchKey"
    with pytest.raises(InstanceProcessingException):
        await s3.copy_object("a", "a")


async def test_fake_delete_forgets_the_timestamp_and_buckets_are_separate() -> None:
    s3 = InMemoryS3Client()
    await s3.upload_bytes("a", b"x")
    await s3.upload_bytes("a", b"y", bucket="other")

    await s3.delete_object("a")
    await s3.upload_bytes("a", b"z")

    head = await s3.head_object("a")
    other = await s3.head_object("a", bucket="other")
    assert head is not None and head.size == 1
    assert (
        other is not None
        and other.etag == hashlib.md5(b"y", usedforsecurity=False).hexdigest()
    )
    assert [i.key async for i in s3.list_objects(prefix="", bucket="other")] == ["a"]


async def test_fake_list_is_sorted_and_filtered_by_prefix() -> None:
    s3 = InMemoryS3Client()
    for key in ("b/2", "a/1", "b/1"):
        await s3.upload_bytes(key, b"x")

    assert [i.key async for i in s3.list_objects(prefix="b/")] == ["b/1", "b/2"]
    assert [i.key async for i in s3.list_objects(prefix="zzz")] == []


async def test_fake_set_last_modified_on_missing_key_raises() -> None:
    s3 = InMemoryS3Client()
    with pytest.raises(FileNotFoundError):
        s3.set_last_modified("nope", datetime.now(UTC))


async def test_fake_set_last_modified_refuses_naive_datetime() -> None:
    """A real bucket reports aware values; a naive one would hide a bug in age maths."""
    s3 = InMemoryS3Client()
    await s3.upload_bytes("a", b"x")
    with pytest.raises(ValueError):
        s3.set_last_modified("a", datetime(2026, 1, 1))


@pytest.mark.parametrize("page_size", [0, 1001])
async def test_fake_list_refuses_page_size_the_adapter_refuses(page_size: int) -> None:
    """A caller passing a size S3 rejects must fail in tests too."""
    s3 = InMemoryS3Client()
    with pytest.raises(ValueError):
        _ = [item async for item in s3.list_objects(prefix="", page_size=page_size)]
