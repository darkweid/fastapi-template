"""
InMemoryCache mechanics only.

Behaviour shared with RedisCache (hits, misses, invalidation, ttl validation,
the enabled switch, decode-and-drop) is asserted for both implementations in
test_cache_contract.py.
"""

from datetime import timedelta

import pytest

from src.core.cache.interface import CacheKey
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.serializer import JsonSerializer
from src.core.utils.datetime_utils import get_utc_now


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache(
        serializer=JsonSerializer(),
        default_ttl=60,
        version_ttl=604800,
    )


KEY = CacheKey(namespace="user:1", suffix="summary")


async def test_value_expires_after_ttl(
    cache: InMemoryCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = get_utc_now()
    monkeypatch.setattr("src.core.cache.memory_cache.get_utc_now", lambda: now)
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    monkeypatch.setattr(
        "src.core.cache.memory_cache.get_utc_now",
        lambda: now + timedelta(seconds=61),
    )

    assert await cache.get(KEY) is None


async def test_ttl_defaults_to_configured_value(cache: InMemoryCache) -> None:
    await cache.set(KEY, {"name": "ada"})

    assert cache.ttl_of(KEY) == 60
