from collections.abc import Sequence
from typing import Any, cast

from redis.asyncio import Redis
import redis.exceptions as redis_exc

from loggers import get_logger
from src.core.cache.base import BaseCache
from src.core.cache.interface import CacheKey, Serializer
from src.core.cache.redis_scripts import (
    CACHE_DELETE_SCRIPT,
    CACHE_GET_SCRIPT,
    CACHE_INVALIDATE_SCRIPT,
    CACHE_SET_SCRIPT,
)
from src.core.redis.degradation import RedisDegradationReporter

logger = get_logger(__name__)

# Only a lost or unresponsive connection is a cache outage worth failing open on.
# Every other RedisError - a ResponseError from a malformed argument or a broken
# Lua script above all - is a programmer error: swallowing it would hide the bug
# and burn the degradation reporter's cooldown on a fake outage, muting the real one.
TRANSPORT_ERRORS = (redis_exc.ConnectionError, redis_exc.TimeoutError)


class RedisCache(BaseCache):
    """
    Redis-backed cache with namespace versioning.

    Reads and writes go through Lua so that resolving every version counter the
    key composes from - the namespace and each of its tags - and touching the
    value happen in a single round trip. Transport failures are
    swallowed (a cache outage must not fail a request); programmer errors -
    unserializable values, a ttl outside (0, version_ttl], a rejected command -
    are raised.
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
        super().__init__(
            serializer,
            prefix=prefix,
            default_ttl=default_ttl,
            version_ttl=version_ttl,
            enabled=enabled,
        )
        self._redis = redis_client
        self._reporter = reporter or RedisDegradationReporter("Cache")

    def _namespace_prefix(self, namespace: str) -> str:
        return f"{self._prefix}:{namespace}"

    async def _eval(self, script: str, counters: Sequence[str], *args: Any) -> Any:
        result = await self._redis.eval(script, len(counters), *counters, *args)
        self._reporter.report_recovered()
        return result

    async def _read_raw(self, key: CacheKey) -> str | None:
        try:
            raw = await self._eval(
                CACHE_GET_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except TRANSPORT_ERRORS as error:
            self._on_transport_error("read", key, error)
            return None
        if raw is None:
            logger.debug("[Cache] miss %s", key)
            return None
        logger.debug("[Cache] hit %s", key)
        return cast(str, raw)

    async def _write_raw(self, key: CacheKey, raw: str, ttl: int) -> None:
        try:
            await self._eval(
                CACHE_SET_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
                raw,
                str(ttl),
            )
        except TRANSPORT_ERRORS as error:
            self._on_transport_error("write", key, error)

    async def _drop(self, key: CacheKey) -> None:
        try:
            await self._eval(
                CACHE_DELETE_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except TRANSPORT_ERRORS as error:
            self._on_transport_error("delete", key, error)

    async def _bump_versions(self, counters: Sequence[str]) -> None:
        try:
            versions = await self._eval(
                CACHE_INVALIDATE_SCRIPT,
                counters,
                str(self._version_ttl),
            )
            logger.debug("[Cache] counters %s bumped to %s", list(counters), versions)
        except TRANSPORT_ERRORS as error:
            logger.warning(
                "[Cache] invalidate failed for counters %s: %s", list(counters), error
            )
            self._reporter.report_degraded(error)

    def _on_transport_error(
        self, operation: str, key: CacheKey, error: redis_exc.RedisError
    ) -> None:
        logger.warning("[Cache] %s failed for %s: %s", operation, key, error)
        self._reporter.report_degraded(error)
