from redis.asyncio import Redis

from src.core.cache.interface import CacheKey
from src.core.cache.keys import tag_version_key, version_key
from src.core.cache.redis_cache import RedisCache
from src.core.cache.serializer import JsonSerializer

PREFIX = "contract"
DEFAULT_TTL_SECONDS = 60
VERSION_TTL_SECONDS = 600
SHRUNK_TTL_SECONDS = 5


def _cache(redis_client: Redis) -> RedisCache:
    return RedisCache(
        redis_client=redis_client,
        serializer=JsonSerializer(),
        prefix=PREFIX,
        default_ttl=DEFAULT_TTL_SECONDS,
        version_ttl=VERSION_TTL_SECONDS,
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
    """Each tag counter is part of the address; bumping any one moves it."""
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
    """A counter must outlive the values addressed through it: one expiring under
    a live value resets the version to 0, and the next invalidation increments it
    straight back onto that value."""
    cache = _cache(redis_backend)
    key = CacheKey("users", "1", tags=("profile",))
    counters = (version_key(PREFIX, "users"), tag_version_key(PREFIX, "profile"))
    await cache.invalidate("users")
    await cache.invalidate_tags("profile")
    for counter in counters:
        await redis_backend.expire(counter, SHRUNK_TTL_SECONDS)

    await cache.set(key, {"name": "Ada"})

    for counter in counters:
        assert SHRUNK_TTL_SECONDS < await redis_backend.ttl(counter)
        assert await redis_backend.ttl(counter) <= VERSION_TTL_SECONDS
