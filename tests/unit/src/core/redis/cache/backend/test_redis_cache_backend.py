from __future__ import annotations

import pytest

from src.core.redis.cache.backend.redis_backend import RedisCacheBackend


class FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.tags: dict[str, set[bytes]] = {}
        self.closed = False

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def sadd(self, tag: str, key: str) -> None:
        if tag not in self.tags:
            self.tags[tag] = set()
        self.tags[tag].add(key.encode())

    async def smembers(self, tag: str) -> set[bytes]:
        return set(self.tags.get(tag, set()))

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def backend() -> RedisCacheBackend:
    instance = RedisCacheBackend()
    instance.redis = None
    return instance


@pytest.mark.asyncio
async def test_backend_returns_none_when_not_initialized(
    backend: RedisCacheBackend,
) -> None:
    assert backend.is_initialized() is False
    assert await backend.get_value("missing") is None
    await backend.set_value("key", b"value", ttl=1)
    await backend.delete("key")


@pytest.mark.asyncio
async def test_backend_get_set_delete(backend: RedisCacheBackend) -> None:
    backend.redis = FakeRedisClient()

    await backend.set_value("key", b"value", ttl=10)
    assert await backend.get_value("key") == b"value"

    await backend.delete("key")
    assert await backend.get_value("key") is None


@pytest.mark.asyncio
async def test_backend_tag_members_normalizes_bytes(
    backend: RedisCacheBackend,
) -> None:
    backend.redis = FakeRedisClient()

    await backend.add_tag("users", "key-1")
    await backend.add_tag("users", "key-2")

    members = await backend.get_tag_members("users")

    assert members == {"key-1", "key-2"}
