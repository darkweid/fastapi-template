from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from loggers import get_logger
from src.core.cache.interface import CacheKey, Serializer
from src.core.cache.keys import value_key, version_key
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class _Entry:
    raw: str
    expires_at: datetime
    ttl: int


class InMemoryCache:
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
        self._serializer = serializer
        self._default_ttl = default_ttl
        self._version_ttl = version_ttl
        self._prefix = prefix
        self._enabled = enabled
        self._entries: dict[str, _Entry] = {}
        self._versions: dict[str, int] = {}

    def _resolve_ttl(self, ttl: int | None) -> int:
        # ttl validation runs before the enabled check, so a disabled cache
        # never hides a configuration error.
        resolved = self._default_ttl if ttl is None else ttl
        if resolved > self._version_ttl:
            raise ValueError(
                f"Cache ttl {resolved}s exceeds version_ttl {self._version_ttl}s: "
                "stale values would outlive their version counter."
            )
        return resolved

    def _current_key(self, key: CacheKey) -> str:
        version = self._versions.get(version_key(self._prefix, key.namespace), 0)
        return value_key(self._prefix, key.namespace, version, key.suffix)

    def _read(self, key: CacheKey) -> str | None:
        entry = self._entries.get(self._current_key(key))
        if entry is None:
            return None
        if get_utc_now() >= entry.expires_at:
            self._entries.pop(self._current_key(key), None)
            return None
        return entry.raw

    def ttl_of(self, key: CacheKey) -> int | None:
        entry = self._entries.get(self._current_key(key))
        return None if entry is None else entry.ttl

    async def get(self, key: CacheKey, *, model: type[T] | None = None) -> Any:
        if not self._enabled:
            return None
        raw = self._read(key)
        if raw is None:
            return None
        return self._serializer.loads(raw, model)

    async def get_raw(self, key: CacheKey) -> str | None:
        return None if not self._enabled else self._read(key)

    async def set(self, key: CacheKey, value: Any, *, ttl: int | None = None) -> None:
        await self.set_raw(key, self._serializer.dumps(value), ttl=ttl)

    async def set_raw(self, key: CacheKey, raw: str, *, ttl: int | None = None) -> None:
        resolved_ttl = self._resolve_ttl(ttl)
        if not self._enabled:
            return
        self._entries[self._current_key(key)] = _Entry(
            raw=raw,
            expires_at=get_utc_now() + timedelta(seconds=resolved_ttl),
            ttl=resolved_ttl,
        )

    async def delete(self, key: CacheKey) -> None:
        self._entries.pop(self._current_key(key), None)

    async def invalidate(self, namespace: str) -> None:
        counter = version_key(self._prefix, namespace)
        self._versions[counter] = self._versions.get(counter, 0) + 1

    async def get_or_set(
        self,
        key: CacheKey,
        factory: Callable[[], Awaitable[T]],
        *,
        ttl: int | None = None,
        model: type[T] | None = None,
    ) -> T:
        cached = await self.get(key, model=model)
        if cached is not None:
            # self.get is typed Any because JsonSerializer.loads is untyped without
            # a model; get_or_set's own contract guarantees the stored value is T.
            return cast(T, cached)
        value = await factory()
        await self.set(key, value, ttl=ttl)
        return value
