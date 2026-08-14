"""
RedisCache mechanics only.

Behaviour shared with InMemoryCache (hits, misses, invalidation, ttl validation,
the enabled switch, decode-and-drop) is asserted for both implementations in
test_cache_contract.py.
"""

from unittest.mock import MagicMock

import pytest
import redis.exceptions as redis_exc

from src.core.cache.interface import CacheKey
from src.core.cache.redis_cache import RedisCache
from src.core.cache.serializer import JsonSerializer
from tests.fakes.redis import InMemoryRedis

KEY = CacheKey(namespace="user:1", suffix="summary")
TAGGED_KEY = CacheKey(namespace="user:1", suffix="summary", tags=("users", "roles"))


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


@pytest.fixture
def sentry_capture(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    capture = MagicMock()
    monkeypatch.setattr(
        "src.core.redis.degradation.sentry_sdk.capture_message", capture
    )
    return capture


async def test_get_uses_single_redis_round_trip(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    fake_redis.cache_eval_calls = 0

    await cache.get(KEY)

    assert fake_redis.cache_eval_calls == 1


async def test_invalidate_sets_version_ttl(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.invalidate(KEY.namespace)

    assert await fake_redis.ttl("cache-ver:user:1") == 604800


async def test_tagged_get_still_uses_a_single_round_trip(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    # Every tag adds a counter to resolve, but all of them are read inside the one
    # script call - a tag must not cost a round trip.
    await cache.set(TAGGED_KEY, {"name": "ada"}, ttl=60)
    fake_redis.cache_eval_calls = 0

    await cache.get(TAGGED_KEY)

    assert fake_redis.cache_eval_calls == 1


async def test_tagged_value_key_composes_every_version(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    # Counters appear in the key's own sorted tag order, after the namespace one.
    await cache.set(TAGGED_KEY, {"name": "ada"}, ttl=30)

    assert await fake_redis.ttl("cache:user:1:v0.0.0:summary") == 30


async def test_write_pushes_every_counter_back_to_the_full_version_ttl(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    # A counter that expires while a value written under it is still alive resets
    # the version to 0, and the next invalidation increments it back onto that
    # value - so a write must leave every counter it read outliving the value.
    await cache.invalidate(TAGGED_KEY.namespace)
    await cache.invalidate_tags(*TAGGED_KEY.tags)
    for counter in ("cache-ver:user:1", "cache-tag:users", "cache-tag:roles"):
        await fake_redis.expire(counter, 10)

    await cache.set(TAGGED_KEY, {"name": "ada"}, ttl=60)

    assert await fake_redis.ttl("cache-ver:user:1") == 604800
    assert await fake_redis.ttl("cache-tag:users") == 604800
    assert await fake_redis.ttl("cache-tag:roles") == 604800


async def test_write_does_not_create_a_counter_that_does_not_exist(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    # An absent counter reads as version 0; the refresh must not materialize it,
    # or every untouched namespace would grow a key it never needed.
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    assert await fake_redis.get("cache-ver:user:1") is None


async def test_invalidate_tags_sets_version_ttl_on_every_counter(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.invalidate_tags("users", "roles")

    assert await fake_redis.get("cache-tag:users") == "1"
    assert await fake_redis.ttl("cache-tag:users") == 604800
    assert await fake_redis.ttl("cache-tag:roles") == 604800


async def test_invalidate_tags_swallows_connection_failure(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    await cache.invalidate_tags("users")


async def test_stored_value_carries_requested_ttl(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=30)

    assert await fake_redis.ttl("cache:user:1:v0:summary") == 30


async def test_get_returns_none_when_redis_is_unreachable(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    assert await cache.get(KEY) is None


async def test_set_swallows_connection_failure(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    await cache.set(KEY, {"name": "ada"}, ttl=60)


async def test_invalidate_swallows_connection_failure(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1)

    await cache.invalidate(KEY.namespace)


async def test_timeout_is_treated_as_a_cache_outage(
    cache: RedisCache, fake_redis: InMemoryRedis
) -> None:
    fake_redis.fail_next_commands(1, error=redis_exc.TimeoutError("slow"))

    assert await cache.get(KEY) is None


async def test_redis_failure_is_reported_once(
    cache: RedisCache, fake_redis: InMemoryRedis, sentry_capture: MagicMock
) -> None:
    fake_redis.fail_next_commands(2)

    await cache.get(KEY)
    await cache.get(KEY)

    assert sentry_capture.call_count == 1


async def test_command_error_propagates_instead_of_faking_an_outage(
    cache: RedisCache, fake_redis: InMemoryRedis, sentry_capture: MagicMock
) -> None:
    # A ResponseError is a bug in the command or the script, not a cache outage:
    # swallowing it would drop the write silently and burn the degradation
    # reporter's cooldown, muting the report of a genuine Redis failure.
    fake_redis.fail_next_commands(1, error=redis_exc.ResponseError("invalid expire"))

    with pytest.raises(redis_exc.ResponseError):
        await cache.set(KEY, {"name": "ada"}, ttl=60)

    sentry_capture.assert_not_called()


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
