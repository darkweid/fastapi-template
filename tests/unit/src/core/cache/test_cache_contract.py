"""
One suite, both Cache implementations.

Everything a caller may rely on regardless of which implementation is bound runs
here against RedisCache (on the fake Redis) and InMemoryCache alike, so a
semantic divergence between the two - the in-memory one is what every other test
in this repository actually exercises - fails immediately instead of surfacing in
production. Storage mechanics that only one of them has (Redis round-trip
counting, Sentry degradation reporting, clock-driven expiry) stay in the
per-implementation modules.
"""

from collections.abc import Callable
from functools import partial

import pytest

from src.core.cache.interface import Cache, CacheKey
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.redis_cache import RedisCache
from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base
from tests.fakes.redis import InMemoryRedis

KEY = CacheKey(namespace="user:1", suffix="summary")
OTHER_KEY = CacheKey(namespace="user:1", suffix="contacts")
VERSION_TTL = 604800


class Sample(Base):
    name: str


@pytest.fixture
def fake_redis() -> InMemoryRedis:
    return InMemoryRedis()


@pytest.fixture(params=["redis", "memory"])
def make_cache(
    request: pytest.FixtureRequest, fake_redis: InMemoryRedis
) -> Callable[..., Cache]:
    if request.param == "redis":
        return partial(
            RedisCache,
            redis_client=fake_redis,
            serializer=JsonSerializer(),
            prefix="cache",
            default_ttl=60,
            version_ttl=VERSION_TTL,
        )
    return partial(
        InMemoryCache,
        serializer=JsonSerializer(),
        default_ttl=60,
        version_ttl=VERSION_TTL,
    )


@pytest.fixture
def cache(make_cache: Callable[..., Cache]) -> Cache:
    return make_cache()


async def test_get_returns_none_on_miss(cache: Cache) -> None:
    assert await cache.get(KEY) is None
    assert await cache.get_raw(KEY) is None


async def test_set_then_get_returns_value(cache: Cache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    assert await cache.get(KEY) == {"name": "ada"}


async def test_typed_get_decodes_into_the_requested_model(cache: Cache) -> None:
    await cache.set(KEY, Sample(name="ada"), ttl=60)

    assert await cache.get(KEY, model=Sample) == Sample(name="ada")


async def test_invalidate_makes_value_unreachable(cache: Cache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)

    await cache.invalidate(KEY.namespace)

    assert await cache.get(KEY) is None


async def test_value_written_after_invalidate_is_readable(cache: Cache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    await cache.invalidate(KEY.namespace)

    await cache.set(KEY, {"name": "grace"}, ttl=60)

    assert await cache.get(KEY) == {"name": "grace"}


async def test_delete_removes_only_the_addressed_key(cache: Cache) -> None:
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    await cache.set(OTHER_KEY, {"phone": "1"}, ttl=60)

    await cache.delete(KEY)

    assert await cache.get(KEY) is None
    assert await cache.get(OTHER_KEY) == {"phone": "1"}


async def test_undecodable_payload_reads_as_a_miss_and_is_dropped(
    cache: Cache,
) -> None:
    await cache.set_raw(KEY, '{"unexpected": 1}', ttl=60)

    assert await cache.get(KEY, model=Sample) is None
    assert await cache.get_raw(KEY) is None


@pytest.mark.parametrize("ttl", [0, -1])
async def test_non_positive_ttl_is_rejected(cache: Cache, ttl: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        await cache.set(KEY, {"name": "ada"}, ttl=ttl)

    with pytest.raises(ValueError, match="positive"):
        await cache.set_raw(KEY, '{"name": "ada"}', ttl=ttl)


async def test_ttl_above_version_ttl_is_rejected(cache: Cache) -> None:
    with pytest.raises(ValueError, match="version_ttl"):
        await cache.set(KEY, {"name": "ada"}, ttl=VERSION_TTL + 1)


async def test_get_or_set_runs_the_factory_once(cache: Cache) -> None:
    calls: list[int] = []

    async def factory() -> Sample:
        calls.append(1)
        return Sample(name="ada")

    first = await cache.get_or_set(KEY, factory, ttl=60, model=Sample)
    second = await cache.get_or_set(KEY, factory, ttl=60, model=Sample)

    assert first == second == Sample(name="ada")
    assert len(calls) == 1


async def test_disabled_cache_neither_stores_nor_reads(
    make_cache: Callable[..., Cache],
) -> None:
    disabled = make_cache(enabled=False)

    await disabled.set(KEY, {"name": "ada"}, ttl=60)

    assert await disabled.get(KEY) is None
    assert await disabled.get_raw(KEY) is None


async def test_disabled_cache_neither_deletes_nor_invalidates(
    cache: Cache, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The switch is flipped on a live instance because that is the only way to
    # observe what a disabled delete/invalidate did: a separately constructed
    # disabled cache has nothing stored to leave alone in the first place.
    await cache.set(KEY, {"name": "ada"}, ttl=60)
    monkeypatch.setattr(cache, "_enabled", False)

    await cache.delete(KEY)
    await cache.invalidate(KEY.namespace)

    monkeypatch.setattr(cache, "_enabled", True)
    assert await cache.get(KEY) == {"name": "ada"}
