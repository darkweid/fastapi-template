import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from redis.asyncio import Redis

from src.core.cache.interface import CacheKey
from src.core.cache.keys import tag_version_key, value_key, version_key
from src.core.cache.redis_cache import RESERVATION_TTL_SECONDS, RedisCache
from src.core.cache.serializer import JsonSerializer
from src.core.redis.degradation import RedisDegradationReporter
from tests.fakes.redis import InMemoryRedis

PREFIX = "contract"
DEFAULT_TTL_SECONDS = 60
VERSION_TTL_SECONDS = 600
SHRUNK_TTL_SECONDS = 5


def _cache(
    redis_client: Redis, *, version_ttl: int = VERSION_TTL_SECONDS
) -> RedisCache:
    return RedisCache(
        redis_client=redis_client,
        serializer=JsonSerializer(),
        prefix=PREFIX,
        default_ttl=DEFAULT_TTL_SECONDS,
        version_ttl=version_ttl,
        # The quiet window would mask a recovery reported by the wrong operation.
        reporter=RedisDegradationReporter("Cache", recovery_quiet_ms=0),
    )


async def test_a_stored_value_is_read_back(redis_backend: Redis) -> None:
    """The read and the write resolve the same version string; if they composed
    it differently every entry would be written and never found."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1", tags=("profile",))

    await cache.set(key, {"name": "Ada"})

    assert await cache.get(key) == {"name": "Ada"}


async def test_an_unknown_key_misses(redis_backend: Redis) -> None:
    """A miss must read as None, not as an error, or every cold cache is a 500."""
    assert await _cache(redis_backend).get(CacheKey("users", "missing")) is None


async def test_invalidating_a_namespace_hides_its_entries(redis_backend: Redis) -> None:
    """After a write the old response must stop being served."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    await cache.set(key, {"name": "Ada"})

    await cache.invalidate("users")

    assert await cache.get(key) is None


async def test_a_value_set_after_invalidation_is_served(redis_backend: Redis) -> None:
    """Invalidation must not poison the namespace: the next fill is a hit."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    await cache.set(key, {"name": "Ada"})
    await cache.invalidate("users")

    await cache.set(key, {"name": "Grace"})

    assert await cache.get(key) == {"name": "Grace"}


async def test_invalidating_a_namespace_leaves_the_others(redis_backend: Redis) -> None:
    """A note write must not cost every user page its cache."""
    cache = _cache(redis_backend)
    user_key = CacheKey("users", "1")
    note_key = CacheKey("notes", "1")
    await cache.set(user_key, {"kind": "user"})
    await cache.set(note_key, {"kind": "note"})

    await cache.invalidate("notes")

    assert await cache.get(user_key) == {"kind": "user"}
    assert await cache.get(note_key) is None


async def test_a_tag_cuts_across_namespaces(redis_backend: Redis) -> None:
    """Tags exist for the entry in another namespace that shows the same object."""
    cache = _cache(redis_backend)
    tagged_user = CacheKey("users", "1", tags=("profile",))
    tagged_note = CacheKey("notes", "1", tags=("profile",))
    untagged = CacheKey("users", "2")
    for key in (tagged_user, tagged_note, untagged):
        await cache.set(key, {"key": str(key)})

    await cache.invalidate_tags("profile")

    assert await cache.get(tagged_user) is None
    assert await cache.get(tagged_note) is None
    assert await cache.get(untagged) == {"key": str(untagged)}


async def test_an_entry_with_two_tags_dies_with_either(redis_backend: Redis) -> None:
    """Each tag counter is part of the address; retiring any one moves it."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1", tags=("profile", "team"))
    await cache.set(key, {"name": "Ada"})

    await cache.invalidate_tags("team")

    assert await cache.get(key) is None


async def test_delete_drops_one_entry(redis_backend: Redis) -> None:
    """Delete is per entry; the rest of the namespace stays warm."""
    cache = _cache(redis_backend)
    dropped = CacheKey("users", "1")
    kept = CacheKey("users", "2")
    await cache.set(dropped, {"n": 1})
    await cache.set(kept, {"n": 2})

    await cache.delete(dropped)

    assert await cache.get(dropped) is None
    assert await cache.get(kept) == {"n": 2}


async def test_a_write_pushes_its_counters_back_to_the_version_ttl(
    redis_backend: Redis,
) -> None:
    """A counter expiring under a live value makes that value unreachable; a
    namespace that is still written to must stay readable, on the generation it
    already has."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1", tags=("profile",))
    counters = (version_key(PREFIX, "users"), tag_version_key(PREFIX, "profile"))
    await cache.set(key, {"name": "Ada"})
    generations = [await redis_backend.get(counter) for counter in counters]
    for counter in counters:
        await redis_backend.expire(counter, SHRUNK_TTL_SECONDS)

    await cache.set(CacheKey("users", "2", tags=("profile",)), {"name": "Grace"})

    assert [await redis_backend.get(counter) for counter in counters] == generations
    for counter in counters:
        assert await redis_backend.ttl(counter) >= VERSION_TTL_SECONDS - 1
    assert await cache.get(key) == {"name": "Ada"}


async def test_a_value_misses_once_its_namespace_counter_is_gone(
    redis_backend: Redis,
) -> None:
    """An evicting cache instance may drop a counter and keep the value: reading
    the value anyway would serve what an invalidation already retired."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    await cache.set(key, {"name": "Ada"})

    await redis_backend.delete(version_key(PREFIX, "users"))

    assert await cache.get(key) is None


async def test_a_tagged_value_misses_when_one_tag_counter_is_gone(
    redis_backend: Redis,
) -> None:
    """The namespace counter surviving is not enough: every counter in the
    address must be present."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1", tags=("profile", "team"))
    await cache.set(key, {"name": "Ada"})

    await redis_backend.delete(tag_version_key(PREFIX, "team"))

    assert await cache.get(key) is None


async def test_a_value_set_after_invalidation_starts_a_new_generation(
    redis_backend: Redis,
) -> None:
    """The refill must not land under the generation the invalidation retired."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    counter = version_key(PREFIX, "users")
    await cache.set(key, {"name": "Ada"})
    retired = await redis_backend.get(counter)
    await cache.invalidate("users")

    await cache.set(key, {"name": "Grace"})

    assert await redis_backend.get(counter) != retired
    assert await cache.get(key) == {"name": "Grace"}


async def test_a_retired_value_stays_unreachable_after_a_sibling_write(
    redis_backend: Redis,
) -> None:
    """The sibling write recreates the namespace counter; a counter that restarted
    at a value it once had would address the retired entry again."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    await cache.set(key, {"name": "Ada"})
    await cache.invalidate("users")

    await cache.set(CacheKey("users", "2"), {"name": "Grace"})

    assert await cache.get(key) is None


async def test_a_write_starts_absent_counters_with_the_version_ttl(
    redis_backend: Redis,
) -> None:
    """A generation is the server clock in microseconds: digits only, never the
    `0` an absent counter used to read as."""
    cache = _cache(redis_backend)
    counters = (
        version_key(PREFIX, "users"),
        tag_version_key(PREFIX, "profile"),
        tag_version_key(PREFIX, "team"),
    )

    await cache.set(CacheKey("users", "1", tags=("profile", "team")), {"n": 1})

    for counter in counters:
        generation = await redis_backend.get(counter)
        assert generation is not None and generation.isdigit()
        assert len(generation) >= 16
        assert await redis_backend.ttl(counter) >= VERSION_TTL_SECONDS - 1


async def test_invalidation_deletes_only_the_named_counters(
    redis_backend: Redis,
) -> None:
    """Retiring a tag must not cost the namespace its generation."""
    cache = _cache(redis_backend)
    await cache.set(CacheKey("users", "1", tags=("profile", "team")), {"n": 1})

    await cache.invalidate_tags("profile", "team")

    assert await redis_backend.get(tag_version_key(PREFIX, "profile")) is None
    assert await redis_backend.get(tag_version_key(PREFIX, "team")) is None
    assert await redis_backend.get(version_key(PREFIX, "users")) is not None


async def test_delete_with_a_missing_counter_touches_nothing(
    redis_backend: Redis,
) -> None:
    """With a counter gone there is no current value; the stored one is left to
    its own ttl rather than guessed at."""
    cache = _cache(redis_backend)
    counter = version_key(PREFIX, "users")
    await cache.set(CacheKey("users", "1"), {"n": 1})
    stored = value_key(PREFIX, "users", await redis_backend.get(counter), "1")
    await redis_backend.delete(counter)

    await cache.delete(CacheKey("users", "1"))

    assert await redis_backend.exists(stored) == 1


@pytest.fixture
def sentry_capture(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    capture = MagicMock()
    monkeypatch.setattr(
        "src.core.redis.degradation.sentry_sdk.capture_message", capture
    )
    return capture


async def test_cache_fails_open_when_redis_is_out_of_memory(
    real_redis: Redis, sentry_capture: MagicMock
) -> None:
    """Pins what no fake can: an OOM raised by redis.call inside EVAL reaches
    redis-py as OutOfMemoryError, not as a generic script error that the
    fail-open set would let through as a 500."""
    cache = _cache(real_redis)
    key = CacheKey("users", "1")
    original = (await real_redis.config_get("maxmemory"))["maxmemory"]
    await real_redis.config_set("maxmemory", 1)
    try:
        await cache.set(key, {"name": "Ada"})
        assert await cache.get(key) is None
    finally:
        await real_redis.config_set("maxmemory", original)

    sentry_capture.assert_called_once()
    assert sentry_capture.call_args.kwargs["tags"]["redis.error"] == "OutOfMemoryError"
    assert sentry_capture.call_args.kwargs["level"] == "error"


@asynccontextmanager
async def _out_of_memory(redis_backend: Redis) -> AsyncIterator[None]:
    if isinstance(redis_backend, InMemoryRedis):
        redis_backend.out_of_memory = True
        yield
        return
    original = (await redis_backend.config_get("maxmemory"))["maxmemory"]
    await redis_backend.config_set("maxmemory", 1)
    try:
        yield
    finally:
        await redis_backend.config_set("maxmemory", original)


async def test_a_sustained_out_of_memory_is_reported_once(
    redis_backend: Redis, sentry_capture: MagicMock
) -> None:
    """Under OOM a read still runs and only the write is refused. Were a read to
    report recovery, every miss would file a recovery and a fresh degradation,
    and the cooldown would never hold."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")

    async with _out_of_memory(redis_backend):
        await cache.set(key, {"name": "Ada"})
        assert await cache.get(key) is None
        await cache.set(key, {"name": "Ada"})
        assert await cache.get(key) is None

    sentry_capture.assert_called_once()
    assert sentry_capture.call_args.kwargs["tags"]["redis.error"] == "OutOfMemoryError"


RACE_KEY = CacheKey("users", "1", tags=("profile", "team"))
RACE_COUNTERS = (
    version_key(PREFIX, "users"),
    tag_version_key(PREFIX, "profile"),
    tag_version_key(PREFIX, "team"),
)


async def _stored_values(client: Redis) -> list[str]:
    """A value under retired generations is out of every read's reach, so only the
    keyspace shows that it was written and holds memory until its ttl."""
    if isinstance(client, InMemoryRedis):
        keys = client.keys_snapshot()
    else:
        keys = [key async for key in client.scan_iter()]
    return [key for key in keys if key.startswith(f"{PREFIX}:users:v")]


async def test_a_reservation_returns_the_generations_it_started(
    redis_backend: Redis,
) -> None:
    """In the order the value key is composed in; another order would store the
    value where no read looks."""
    cache = _cache(redis_backend)
    await cache.reserve(CacheKey("notes", "1", tags=("team",)))

    reservation = await cache.reserve(RACE_KEY)

    stored = tuple([await redis_backend.get(counter) for counter in RACE_COUNTERS])
    assert reservation == stored
    assert len(set(stored)) == 2
    for counter in RACE_COUNTERS:
        remaining = await redis_backend.ttl(counter)
        assert RESERVATION_TTL_SECONDS - 1 <= remaining <= RESERVATION_TTL_SECONDS
    await cache.set_reserved(RACE_KEY, '{"n": 1}', reservation)
    for counter in RACE_COUNTERS:
        assert await redis_backend.ttl(counter) >= VERSION_TTL_SECONDS - 1
    assert await cache.get(RACE_KEY) == {"n": 1}


@pytest.mark.parametrize("counter", RACE_COUNTERS)
async def test_a_value_is_not_stored_once_any_counter_is_gone(
    redis_backend: Redis, counter: str
) -> None:
    """Invalidated or evicted, the counter's absence means the value may predate
    an invalidation. The refused write leaves no counter behind either."""
    cache = _cache(redis_backend)
    reservation = await cache.reserve(RACE_KEY)
    await redis_backend.delete(counter)

    await cache.set_reserved(RACE_KEY, '{"n": 1}', reservation)

    assert await redis_backend.exists(counter) == 0
    assert await _stored_values(redis_backend) == []
    assert await cache.get(RACE_KEY) is None


@pytest.mark.parametrize("counter", RACE_COUNTERS)
async def test_a_value_is_not_stored_once_any_counter_restarted(
    redis_backend: Redis, counter: str
) -> None:
    """Another reader's miss brings the counter back: the write must compare the
    generation, not the presence."""
    cache = _cache(redis_backend)
    reservation = await cache.reserve(RACE_KEY)
    await redis_backend.delete(counter)
    restarted = await cache.reserve(RACE_KEY)

    await cache.set_reserved(RACE_KEY, '{"n": "stale"}', reservation)

    assert restarted != reservation
    assert await _stored_values(redis_backend) == []
    assert await cache.get(RACE_KEY) is None
    await cache.set_reserved(RACE_KEY, '{"n": "fresh"}', restarted)
    await cache.set_reserved(RACE_KEY, '{"n": "stale"}', reservation)
    assert await cache.get(RACE_KEY) == {"n": "fresh"}


async def test_a_factory_that_raises_leaves_only_short_lived_counters(
    redis_backend: Redis,
) -> None:
    """A scan over missing ids reserves a namespace per id and stores nothing; at
    the version ttl that fills a Redis that evicts nothing."""

    async def missing() -> None:
        raise LookupError

    with pytest.raises(LookupError):
        await _cache(redis_backend).get_or_set(RACE_KEY, missing)

    for counter in RACE_COUNTERS:
        assert 0 < await redis_backend.ttl(counter) <= RESERVATION_TTL_SECONDS


async def test_a_reservation_does_not_outlive_the_version_ttl(
    redis_backend: Redis,
) -> None:
    cache = _cache(redis_backend, version_ttl=DEFAULT_TTL_SECONDS)

    await cache.reserve(RACE_KEY)

    for counter in RACE_COUNTERS:
        assert 0 < await redis_backend.ttl(counter) <= DEFAULT_TTL_SECONDS


async def test_a_reservation_extends_a_counter_about_to_expire(
    redis_backend: Redis,
) -> None:
    """The second reader of a cold key gets as long to compute as the first. A full
    Redis still extends: EXPIRE takes no memory."""
    cache = _cache(redis_backend)
    first = await cache.reserve(RACE_KEY)
    for counter in RACE_COUNTERS:
        await redis_backend.expire(counter, SHRUNK_TTL_SECONDS)

    async with _out_of_memory(redis_backend):
        assert await cache.reserve(RACE_KEY) == first

    for counter in RACE_COUNTERS:
        assert await redis_backend.ttl(counter) >= RESERVATION_TTL_SECONDS - 1


async def test_a_full_redis_starts_no_counter_beside_an_expiring_one(
    redis_backend: Redis,
) -> None:
    """A script is refused for memory only until its first write: an EXPIRE ahead of
    the SET would let the SET through."""
    cache = _cache(redis_backend)
    await cache.reserve(RACE_KEY)
    kept, *dropped = RACE_COUNTERS
    await redis_backend.expire(kept, SHRUNK_TTL_SECONDS)
    await redis_backend.delete(*dropped)

    async with _out_of_memory(redis_backend):
        assert await cache.reserve(RACE_KEY) is None

    assert await redis_backend.exists(*dropped) == 0
    assert await redis_backend.ttl(kept) <= SHRUNK_TTL_SECONDS


async def test_a_reservation_leaves_a_longer_lived_counter_alone(
    redis_backend: Redis,
) -> None:
    """A counter cut short would take the values stored under it along."""
    cache = _cache(redis_backend)
    await cache.set(RACE_KEY, {"n": 1})

    await cache.reserve(RACE_KEY)

    for counter in RACE_COUNTERS:
        assert await redis_backend.ttl(counter) >= VERSION_TTL_SECONDS - 1


async def test_a_value_is_not_stored_once_its_reservation_expired(
    redis_backend: Redis,
) -> None:
    cache = _cache(redis_backend)
    reservation = await cache.reserve(RACE_KEY)
    for counter in RACE_COUNTERS:
        await redis_backend.expire(counter, 0)

    await cache.set_reserved(RACE_KEY, '{"n": 1}', reservation)

    assert await _stored_values(redis_backend) == []
    assert await redis_backend.exists(*RACE_COUNTERS) == 0


async def test_a_miss_never_reserves_by_reading(redis_backend: Redis) -> None:
    """GET stays read-only: a full Redis refuses a script that writes, and reads
    must keep working there."""
    cache = _cache(redis_backend)

    assert await cache.get(RACE_KEY) is None

    assert await redis_backend.exists(*RACE_COUNTERS) == 0


async def test_a_full_redis_stores_nothing_and_keeps_serving_hits(
    redis_backend: Redis, sentry_capture: MagicMock
) -> None:
    """With the counters present a reservation only reads, so it is granted and the
    write is what is refused; without them the reservation is refused. Either way
    the value is computed, and neither is a recovery."""
    cache = _cache(redis_backend)
    hit = CacheKey("users", "hit")
    await cache.set(hit, {"n": 1})

    async def compute() -> dict[str, int]:
        return {"n": 2}

    async with _out_of_memory(redis_backend):
        assert await cache.reserve(hit) is not None
        assert await cache.reserve(CacheKey("notes", "1")) is None
        assert await cache.get_or_set(CacheKey("users", "2"), compute) == {"n": 2}
        assert await cache.get_or_set(CacheKey("notes", "1"), compute) == {"n": 2}
        assert await cache.get(hit) == {"n": 1}
        assert await cache.get(CacheKey("users", "2")) is None
        assert await redis_backend.exists(version_key(PREFIX, "notes")) == 0

    sentry_capture.assert_called_once()


async def test_an_invalidation_racing_the_write_never_leaves_the_stale_value(
    real_redis: Redis, redis_client_factory: Callable[[], Redis]
) -> None:
    """Whichever of the two scripts runs first, the value computed before the
    invalidation is gone after both."""
    for attempt in range(50):
        key = CacheKey("users", str(attempt), tags=("profile",))
        writer, invalidator = (_cache(redis_client_factory()) for _ in range(2))
        reservation = await writer.reserve(key)

        await asyncio.gather(
            writer.set_reserved(key, '{"n": "stale"}', reservation),
            invalidator.invalidate_tags("profile"),
        )

        assert await writer.get(key) is None


async def test_a_value_misses_after_its_counter_is_evicted_and_a_sibling_writes(
    redis_backend: Redis,
) -> None:
    """An evicted counter must not restart at a generation an earlier value still
    lives under: read as 0 when absent, the sibling write would bring back the
    generation the first write used, and the value invalidated since would be
    served again."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1")
    await cache.set(key, {"name": "Ada"})
    await cache.invalidate("users")
    await cache.set(key, {"name": "Grace"})
    await redis_backend.delete(version_key(PREFIX, "users"))

    await cache.set(CacheKey("users", "2"), {"name": "Grace"})

    assert await cache.get(key) is None
