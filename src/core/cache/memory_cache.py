from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    """Process-local cache with the same namespace-version semantics as RedisCache."""

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
        self._versions: dict[str, int] = {}

    def _current_key(self, key: CacheKey) -> str:
        version = ".".join(
            str(self._versions.get(counter, 0)) for counter in self._counter_keys(key)
        )
        return value_key(self._prefix, key.namespace, version, key.suffix)

    def ttl_of(self, key: CacheKey) -> int | None:
        entry = self._entries.get(self._current_key(key))
        return None if entry is None else entry.ttl

    async def _read_raw(self, key: CacheKey) -> str | None:
        entry = self._entries.get(self._current_key(key))
        if entry is None:
            return None
        if get_utc_now() >= entry.expires_at:
            self._entries.pop(self._current_key(key), None)
            return None
        return entry.raw

    async def _write_raw(self, key: CacheKey, raw: str, ttl: int) -> None:
        self._entries[self._current_key(key)] = _Entry(
            raw=raw,
            expires_at=get_utc_now() + timedelta(seconds=ttl),
            ttl=ttl,
        )

    async def _drop(self, key: CacheKey) -> None:
        self._entries.pop(self._current_key(key), None)

    async def _bump_versions(self, counters: Sequence[str]) -> None:
        for counter in counters:
            self._versions[counter] = self._versions.get(counter, 0) + 1
