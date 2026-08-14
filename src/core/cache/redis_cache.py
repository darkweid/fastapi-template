from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from pydantic import ValidationError
from redis.asyncio import Redis
import redis.exceptions as redis_exc

from loggers import get_logger
from src.core.cache.interface import CacheKey, Serializer
from src.core.cache.keys import version_key
from src.core.cache.redis_scripts import (
    CACHE_DELETE_SCRIPT,
    CACHE_GET_SCRIPT,
    CACHE_INVALIDATE_SCRIPT,
    CACHE_SET_SCRIPT,
)
from src.core.redis.degradation import RedisDegradationReporter

logger = get_logger(__name__)

T = TypeVar("T")


class RedisCache:
    """
    Redis-backed cache with namespace versioning.

    Reads and writes go through Lua so that resolving the namespace version and
    touching the value happen in a single round trip. Transport failures are
    swallowed (a cache outage must not fail a request); programmer errors -
    unserializable values, a ttl above version_ttl - are raised.
    """

    def __init__(
        self,
        redis_client: Redis,
        serializer: Serializer,
        *,
        prefix: str,
        default_ttl: int,
        version_ttl: int,
        enabled: bool = True,
        reporter: RedisDegradationReporter | None = None,
    ) -> None:
        self._redis = redis_client
        self._serializer = serializer
        self._prefix = prefix
        self._default_ttl = default_ttl
        self._version_ttl = version_ttl
        self._enabled = enabled
        self._reporter = reporter or RedisDegradationReporter("Cache")

    def _resolve_ttl(self, ttl: int | None) -> int:
        resolved = self._default_ttl if ttl is None else ttl
        if resolved > self._version_ttl:
            raise ValueError(
                f"Cache ttl {resolved}s exceeds version_ttl {self._version_ttl}s: "
                "stale values would outlive their version counter."
            )
        return resolved

    def _namespace_prefix(self, namespace: str) -> str:
        return f"{self._prefix}:{namespace}"

    async def _eval(self, script: str, *args: Any) -> Any:
        result = await self._redis.eval(script, 1, *args)
        self._reporter.report_recovered()
        return result

    async def get_raw(self, key: CacheKey) -> str | None:
        if not self._enabled:
            return None
        try:
            raw = await self._eval(
                CACHE_GET_SCRIPT,
                version_key(self._prefix, key.namespace),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except redis_exc.RedisError as error:
            self._on_transport_error("read", key, error)
            return None
        if raw is None:
            logger.debug("[Cache] miss %s", key)
            return None
        logger.debug("[Cache] hit %s", key)
        return cast(str, raw)

    async def get(self, key: CacheKey, *, model: type[T] | None = None) -> Any:
        raw = await self.get_raw(key)
        if raw is None:
            return None
        try:
            return self._serializer.loads(raw, model)
        except (ValueError, ValidationError):
            logger.warning("[Cache] stale payload for %s, dropping key", key)
            await self.delete(key)
            return None

    async def set(self, key: CacheKey, value: Any, *, ttl: int | None = None) -> None:
        await self.set_raw(key, self._serializer.dumps(value), ttl=ttl)

    async def set_raw(self, key: CacheKey, raw: str, *, ttl: int | None = None) -> None:
        resolved_ttl = self._resolve_ttl(ttl)
        if not self._enabled:
            return
        try:
            await self._eval(
                CACHE_SET_SCRIPT,
                version_key(self._prefix, key.namespace),
                self._namespace_prefix(key.namespace),
                key.suffix,
                raw,
                str(resolved_ttl),
            )
        except redis_exc.RedisError as error:
            self._on_transport_error("write", key, error)

    async def delete(self, key: CacheKey) -> None:
        if not self._enabled:
            return
        try:
            await self._eval(
                CACHE_DELETE_SCRIPT,
                version_key(self._prefix, key.namespace),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except redis_exc.RedisError as error:
            self._on_transport_error("delete", key, error)

    async def invalidate(self, namespace: str) -> None:
        if not self._enabled:
            return
        try:
            version = await self._eval(
                CACHE_INVALIDATE_SCRIPT,
                version_key(self._prefix, namespace),
                str(self._version_ttl),
            )
            logger.debug("[Cache] namespace %s bumped to v%s", namespace, version)
        except redis_exc.RedisError as error:
            logger.warning(
                "[Cache] invalidate failed for namespace %s: %s", namespace, error
            )
            self._reporter.report_degraded(error)

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
            return cast(T, cached)
        value = await factory()
        await self.set(key, value, ttl=ttl)
        return value

    def _on_transport_error(
        self, operation: str, key: CacheKey, error: redis_exc.RedisError
    ) -> None:
        logger.warning("[Cache] %s failed for %s: %s", operation, key, error)
        self._reporter.report_degraded(error)
