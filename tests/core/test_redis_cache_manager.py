from __future__ import annotations

from typing import Any

import pytest

from src.core.redis.cache.coder.json_coder import JsonCoder
from src.core.redis.cache.manager.manager import CacheManager
from tests.fakes.cache import FakeCacheBackend


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, value: int) -> dict[str, Any]:
        self.calls += 1
        return {"value": value, "user_id": "user-1"}


COUNTER = Counter()


async def compute_value(value: int) -> dict[str, Any]:
    return await COUNTER.run(value)


@pytest.mark.asyncio
async def test_cache_manager_raises_if_backend_not_initialized() -> None:
    backend = FakeCacheBackend(initialized=False)
    manager = CacheManager(backend=backend, coder=JsonCoder())
    decorated = manager.decorator(ttl=10, tags=["tag"])(compute_value)

    with pytest.raises(RuntimeError, match="Cache backend is not initialized"):
        await decorated(1)


@pytest.mark.asyncio
async def test_cache_manager_cache_miss_then_hit() -> None:
    backend = FakeCacheBackend()
    manager = CacheManager(backend=backend, coder=JsonCoder())
    decorated = manager.decorator(ttl=10, tags=["tag"])(compute_value)

    COUNTER.calls = 0
    first = await decorated(1)
    second = await decorated(1)

    assert first == second
    assert COUNTER.calls == 1
    assert backend._tags.get("tag")
    assert backend._tags.get("user-1")


@pytest.mark.asyncio
async def test_cache_manager_invalidate_tags() -> None:
    backend = FakeCacheBackend()
    manager = CacheManager(backend=backend, coder=JsonCoder())

    backend.seed("key-1", b"1")
    backend.seed("key-2", b"2")
    await backend.add_tag("tag", "key-1")
    await backend.add_tag("tag", "key-2")

    await manager.invalidate_tags(["tag"])

    assert "key-1" in backend.invalidated_keys
    assert "key-2" in backend.invalidated_keys
