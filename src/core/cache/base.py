from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar, cast

from pydantic import ValidationError

from loggers import get_logger
from src.core.cache.interface import CacheKey, Serializer
from src.core.cache.keys import tag_version_key, version_key

logger = get_logger(__name__)

T = TypeVar("T")


class BaseCache(ABC):
    """
    Semantics shared by every Cache implementation.

    Subclasses supply storage only - reading, writing, dropping a key and bumping
    version counters. TTL validation, the `enabled` switch, serialization, key
    composition and the decode-and-drop rule live here, so the in-memory
    implementation the tests run against cannot drift from the Redis one that
    serves production traffic.
    """

    def __init__(
        self,
        serializer: Serializer,
        *,
        prefix: str,
        default_ttl: int,
        version_ttl: int,
        enabled: bool,
    ) -> None:
        self._serializer = serializer
        self._prefix = prefix
        self._default_ttl = default_ttl
        self._version_ttl = version_ttl
        self._enabled = enabled

    def _counter_keys(self, key: CacheKey) -> list[str]:
        """
        Every version counter an entry's address is composed from.

        Order is fixed - namespace first, then tags in the key's own sorted order -
        because the composed version string is part of the value key: a different
        order would address a different entry.
        """
        counters = [version_key(self._prefix, key.namespace)]
        counters.extend(tag_version_key(self._prefix, tag) for tag in key.tags)
        return counters

    def _resolve_ttl(self, ttl: int | None) -> int:
        # TTL validation runs before the enabled check, so a disabled cache never
        # hides a configuration error until the day someone turns it on.
        resolved = self._default_ttl if ttl is None else ttl
        if resolved <= 0:
            raise ValueError(
                f"Cache ttl must be a positive number of seconds, got {resolved}."
            )
        if resolved > self._version_ttl:
            raise ValueError(
                f"Cache ttl {resolved}s exceeds version_ttl {self._version_ttl}s: "
                "stale values would outlive their version counter."
            )
        return resolved

    async def get(self, key: CacheKey, *, model: type[T] | None = None) -> Any:
        raw = await self.get_raw(key)
        if raw is None:
            return None
        try:
            return self._serializer.loads(raw, model)
        except (ValueError, ValidationError):
            # A payload written by an older shape of the model is a miss, not an
            # error: drop it so the next write stores the current shape.
            logger.warning("[Cache] stale payload for %s, dropping key", key)
            await self.delete(key)
            return None

    async def set(self, key: CacheKey, value: Any, *, ttl: int | None = None) -> None:
        await self.set_raw(key, self._serializer.dumps(value), ttl=ttl)

    async def get_raw(self, key: CacheKey) -> str | None:
        if not self._enabled:
            return None
        return await self._read_raw(key)

    async def set_raw(self, key: CacheKey, raw: str, *, ttl: int | None = None) -> None:
        resolved_ttl = self._resolve_ttl(ttl)
        if not self._enabled:
            return
        await self._write_raw(key, raw, resolved_ttl)

    async def delete(self, key: CacheKey) -> None:
        if not self._enabled:
            return
        await self._drop(key)

    async def invalidate(self, namespace: str) -> None:
        if not self._enabled:
            return
        await self._bump_versions([version_key(self._prefix, namespace)])

    async def invalidate_tags(self, *tags: str) -> None:
        """
        Drop every entry carrying any of these tags, across all namespaces.

        Each tag is one counter, so the cost is one increment per tag regardless
        of how many entries carry it - nothing is scanned and nothing is listed.
        """
        if not self._enabled or not tags:
            return
        counters = [tag_version_key(self._prefix, tag) for tag in dict.fromkeys(tags)]
        await self._bump_versions(counters)

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
            # get() is typed Any because JsonSerializer.loads is untyped without a
            # model; get_or_set's own contract guarantees the stored value is T.
            return cast(T, cached)
        value = await factory()
        await self.set(key, value, ttl=ttl)
        return value

    @abstractmethod
    async def _read_raw(self, key: CacheKey) -> str | None: ...

    @abstractmethod
    async def _write_raw(self, key: CacheKey, raw: str, ttl: int) -> None: ...

    @abstractmethod
    async def _drop(self, key: CacheKey) -> None: ...

    @abstractmethod
    async def _bump_versions(self, counters: Sequence[str]) -> None: ...
