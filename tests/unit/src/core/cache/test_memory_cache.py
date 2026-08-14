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


async def test_get_returns_none_on_miss(cache: InMemoryCache) -> None:
    assert await cache.get(KEY) is None


async def test_set_then_get_returns_value(cache: InMemoryCache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    assert await cache.get(KEY) == {"name": "ada"}


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


async def test_invalidate_makes_value_unreachable(cache: InMemoryCache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    await cache.invalidate(KEY.namespace)

    assert await cache.get(KEY) is None


async def test_value_written_after_invalidate_is_readable(cache: InMemoryCache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    await cache.invalidate(KEY.namespace)

    await cache.set(KEY, {"name": "grace"}, ttl=60)

    assert await cache.get(KEY) == {"name": "grace"}


async def test_delete_removes_single_key(cache: InMemoryCache) -> None:
    other = CacheKey(namespace="user:1", suffix="contacts")
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    await cache.set(other, {"phone": "1"}, ttl=60)

    await cache.delete(KEY)

    assert await cache.get(KEY) is None
    assert await cache.get(other) == {"phone": "1"}


async def test_set_rejects_ttl_above_version_ttl(cache: InMemoryCache) -> None:
    with pytest.raises(ValueError, match="version_ttl"):
        await cache.set(KEY, {"name": "ada"}, ttl=604801)


async def test_ttl_defaults_to_configured_value(cache: InMemoryCache) -> None:
    await cache.set(KEY, {"name": "ada"})

    assert cache.ttl_of(KEY) == 60


async def test_get_or_set_calls_factory_once(cache: InMemoryCache) -> None:
    calls: list[int] = []

    async def factory() -> dict[str, str]:
        calls.append(1)
        return {"name": "ada"}

    first = await cache.get_or_set(KEY, factory, ttl=60)
    second = await cache.get_or_set(KEY, factory, ttl=60)

    assert first == second == {"name": "ada"}
    assert len(calls) == 1


async def test_disabled_cache_never_stores(cache: InMemoryCache) -> None:
    disabled = InMemoryCache(
        serializer=JsonSerializer(),
        default_ttl=60,
        version_ttl=604800,
        enabled=False,
    )

    await disabled.set(KEY, {"name": "ada"}, ttl=60)

    assert await disabled.get(KEY) is None
