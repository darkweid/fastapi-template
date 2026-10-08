from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count

from src.core.cache.base import BaseCache
from src.core.cache.interface import CacheKey, Serializer
from src.core.cache.keys import value_key
from src.core.utils.datetime_utils import get_utc_now


@dataclass(slots=True)
class _Entry:
    raw: str
    expires_at: datetime
    ttl: int


class InMemoryCache(BaseCache):
    """
    Process-local cache with the same version-counter semantics as RedisCache.

    A missing counter is a miss, a write materializes every counter it needs with
    a generation never handed out before, and invalidation deletes counters. The
    process never evicts a counter, but keeping the rules identical is what lets
    the shared cache contract run against both implementations.
    """

    def __init__(
        self,
        serializer: Serializer,
        *,
        default_ttl: int,
        version_ttl: int,
        prefix: str = "cache",
        enabled: bool = True,
    ) -> None:
        super().__init__(
            serializer,
            prefix=prefix,
            default_ttl=default_ttl,
            version_ttl=version_ttl,
            enabled=enabled,
        )
        self._entries: dict[str, _Entry] = {}
        self._versions: dict[str, str] = {}
        self._generations = count(1)

    def _current_key(self, key: CacheKey) -> str | None:
        versions = [self._versions.get(counter) for counter in self._counter_keys(key)]
        if any(version is None for version in versions):
            return None
        return value_key(
            self._prefix,
            key.namespace,
            ".".join(version for version in versions if version is not None),
            key.suffix,
        )

    def ttl_of(self, key: CacheKey) -> int | None:
        current_key = self._current_key(key)
        entry = None if current_key is None else self._entries.get(current_key)
        return None if entry is None else entry.ttl

    async def _read_raw(self, key: CacheKey) -> str | None:
        current_key = self._current_key(key)
        if current_key is None:
            return None
        entry = self._entries.get(current_key)
        if entry is None:
            return None
        if get_utc_now() >= entry.expires_at:
            self._entries.pop(current_key, None)
            return None
        return entry.raw

    async def _write_raw(self, key: CacheKey, raw: str, ttl: int) -> None:
        generation = str(next(self._generations))
        for counter in self._counter_keys(key):
            self._versions.setdefault(counter, generation)
        current_key = self._current_key(key)
        if current_key is None:
            return
        self._entries[current_key] = _Entry(
            raw=raw,
            expires_at=get_utc_now() + timedelta(seconds=ttl),
            ttl=ttl,
        )

    async def _drop(self, key: CacheKey) -> None:
        current_key = self._current_key(key)
        if current_key is not None:
            self._entries.pop(current_key, None)

    async def _retire_counters(self, counters: Sequence[str]) -> None:
        for counter in counters:
            self._versions.pop(counter, None)
