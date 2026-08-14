from unittest.mock import MagicMock

import pytest

from src.core.cache.interface import CacheKey
from src.core.cache.redis_cache import RedisCache
from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base
from tests.fakes.redis import InMemoryRedis

KEY = CacheKey(namespace="user:1", suffix="summary")


class Sample(Base):
    name: str


@pytest.fixture
def fake_redis() -> InMemoryRedis:
    return InMemoryRedis()


@pytest.fixture
def cache(fake_redis: InMemoryRedis) -> RedisCache:
    return RedisCache(
        redis_client=fake_redis,
        serializer=JsonSerializer(),
        prefix="cache",
        default_ttl=60,
        version_ttl=604800,
    )


async def test_set_then_get_returns_value(cache: RedisCache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    assert await cache.get(KEY) == {"name": "ada"}


async def test_get_uses_single_redis_round_trip(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    fake_redis.cache_eval_calls = 0

    await cache.get(KEY)

    assert fake_redis.cache_eval_calls == 1


async def test_invalidate_makes_value_unreachable(cache: RedisCache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    await cache.invalidate(KEY.namespace)

    assert await cache.get(KEY) is None


async def test_invalidate_sets_version_ttl(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.invalidate(KEY.namespace)

    assert await fake_redis.ttl("cache-ver:user:1") == 604800


async def test_stored_value_carries_requested_ttl(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=30)

    assert await fake_redis.ttl("cache:user:1:v0:summary") == 30


async def test_set_rejects_ttl_above_version_ttl(cache: RedisCache) -> None:
    with pytest.raises(ValueError, match="version_ttl"):
        await cache.set(KEY, {"name": "ada"}, ttl=604801)


async def test_get_returns_none_when_redis_fails(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    assert await cache.get(KEY) is None


async def test_set_swallows_redis_failure(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    await cache.set(KEY, {"name": "ada"}, ttl=60)


async def test_invalidate_swallows_redis_failure(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    await cache.invalidate(KEY.namespace)


async def test_redis_failure_is_reported_once(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = MagicMock()
    monkeypatch.setattr(
        "src.core.redis.degradation.sentry_sdk.capture_message", capture
    )
    cache = RedisCache(
        redis_client=fake_redis,
        serializer=JsonSerializer(),
        prefix="cache",
        default_ttl=60,
        version_ttl=604800,
    )
    fake_redis.fail_next_commands(2)

    await cache.get(KEY)
    await cache.get(KEY)

    assert capture.call_count == 1


async def test_invalid_payload_is_treated_as_miss_and_deleted(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await fake_redis.set("cache:user:1:v0:summary", '{"unexpected": 1}')

    assert await cache.get(KEY, model=Sample) is None
    assert await fake_redis.get("cache:user:1:v0:summary") is None


async def test_disabled_cache_never_touches_redis(fake_redis: InMemoryRedis) -> None:
    cache = RedisCache(
        redis_client=fake_redis,
        serializer=JsonSerializer(),
        prefix="cache",
        default_ttl=60,
        version_ttl=604800,
        enabled=False,
    )

    await cache.set(KEY, {"name": "ada"}, ttl=60)

    assert await cache.get(KEY) is None
    assert fake_redis.cache_eval_calls == 0
