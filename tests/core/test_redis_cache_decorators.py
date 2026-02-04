from __future__ import annotations

from typing import Any

import pytest

from src.core.redis.cache.decorators import cache, redis_backend


class FakeRedisClient:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.tags: dict[str, set[bytes]] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def sadd(self, tag: str, key: str) -> None:
        self.tags.setdefault(tag, set()).add(key.encode())

    async def smembers(self, tag: str) -> set[bytes]:
        return set(self.tags.get(tag, set()))


class CacheCounter:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, value: int) -> dict[str, Any]:
        self.calls += 1
        return {"value": value}


async def compute_value(counter: CacheCounter, value: int) -> dict[str, Any]:
    return await counter.run(value)


@pytest.fixture
def cache_backend() -> FakeRedisClient:
    previous = redis_backend.redis
    backend = FakeRedisClient()
    redis_backend.redis = backend
    yield backend
    redis_backend.redis = previous


@pytest.mark.asyncio
async def test_cache_decorator_uses_global_backend(
    cache_backend: FakeRedisClient,
) -> None:
    counter = CacheCounter()
    decorated = cache.decorator(ttl=10, tags=["tag"])(compute_value)

    first = await decorated(counter, 1)
    second = await decorated(counter, 1)

    assert first == second
    assert counter.calls == 1
    assert await redis_backend.get_tag_members("tag")
