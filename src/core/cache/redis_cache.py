from collections.abc import Sequence
from typing import Any, cast

from redis.asyncio import Redis
import redis.exceptions as redis_exc

from loggers import get_logger
from src.core.cache.base import BaseCache
from src.core.cache.interface import CacheKey, CacheReservation, Serializer
from src.core.cache.redis_scripts import (
    CACHE_DELETE_SCRIPT,
    CACHE_GET_SCRIPT,
    CACHE_INVALIDATE_SCRIPT,
    CACHE_RESERVE_SCRIPT,
    CACHE_SET_SCRIPT,
)
from src.core.errors.describe import describe_error
from src.core.redis.degradation import RedisDegradationReporter

logger = get_logger(__name__)

# How long a miss may take between its reservation and its write; a value computed
# for longer is not stored. A miss that stores nothing leaves its counters for as
# long, and a path-derived namespace makes one per guessed id.
RESERVATION_TTL_SECONDS = 300

# A lost or unresponsive connection is a cache outage worth failing open on.
# Every other RedisError - a ResponseError from a malformed argument or a broken
# Lua script above all - is a programmer error: swallowing it would hide the bug
# and burn the degradation reporter's cooldown on a fake outage, muting the real one.
TRANSPORT_ERRORS = (redis_exc.ConnectionError, redis_exc.TimeoutError)
# The one ResponseError that is an outage: under noeviction a full Redis refuses
# writes with OOM. Raising it would turn every cache miss into a 500 while the
# instance the cache helped fill is already in trouble.
CAPACITY_ERRORS = (redis_exc.OutOfMemoryError,)
FAIL_OPEN_ERRORS = TRANSPORT_ERRORS + CAPACITY_ERRORS


class RedisCache(BaseCache):
    """
    Redis-backed cache with namespace versioning.

    Reads and writes go through Lua so that resolving every version counter the
    key composes from - the namespace and each of its tags - and touching the
    value happen in a single round trip. Transport failures and OOM refusals are
    swallowed (a cache outage must not fail a request); programmer errors -
    unserializable values, a ttl outside (0, version_ttl], a rejected command -
    are raised. Only a stored value reports recovery.
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
        return await self._redis.eval(script, len(counters), *counters, *args)

    async def _read_raw(self, key: CacheKey) -> str | None:
        try:
            raw = await self._eval(
                CACHE_GET_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except FAIL_OPEN_ERRORS as error:
            self._on_redis_outage("read", key, error)
            return None
        if raw is None:
            logger.debug("[Cache] miss %s", key)
            return None
        logger.debug("[Cache] hit %s", key)
        return cast(str, raw)

    async def _reserve_counters(
        self, counters: Sequence[str]
    ) -> CacheReservation | None:
        try:
            versions = await self._eval(
                CACHE_RESERVE_SCRIPT,
                counters,
                str(min(self._version_ttl, RESERVATION_TTL_SECONDS)),
            )
        except FAIL_OPEN_ERRORS as error:
            self._on_counters_outage("reserve", counters, error)
            return None
        # Not a recovery: with every counter present this read and extended them at
        # most, which a full Redis still does.
        return tuple(versions)

    async def _write_raw(
        self, key: CacheKey, raw: str, ttl: int, reservation: CacheReservation
    ) -> None:
        try:
            stored = await self._eval(
                CACHE_SET_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
                raw,
                str(ttl),
                str(self._version_ttl),
                *reservation,
            )
        except FAIL_OPEN_ERRORS as error:
            self._on_redis_outage("write", key, error)
            return
        if not stored:
            logger.debug("[Cache] %s invalidated while computed, not stored", key)
            return
        # Only a write proves recovery: under OOM a read or a DEL still succeeds,
        # so recovering on one would restart the cooldown on every miss.
        self._reporter.report_recovered()

    async def _drop(self, key: CacheKey) -> None:
        try:
            await self._eval(
                CACHE_DELETE_SCRIPT,
                self._counter_keys(key),
                self._namespace_prefix(key.namespace),
                key.suffix,
            )
        except FAIL_OPEN_ERRORS as error:
            self._on_redis_outage("delete", key, error)

    async def _retire_counters(self, counters: Sequence[str]) -> None:
        try:
            await self._eval(CACHE_INVALIDATE_SCRIPT, counters)
            logger.debug("[Cache] counters %s retired", list(counters))
        except FAIL_OPEN_ERRORS as error:
            self._on_counters_outage("invalidate", counters, error)

    def _on_counters_outage(
        self, operation: str, counters: Sequence[str], error: redis_exc.RedisError
    ) -> None:
        logger.warning(
            "[Cache] %s failed for counters %s: %s",
            operation,
            list(counters),
            describe_error(error),
        )
        self._reporter.report_degraded(error)

    def _on_redis_outage(
        self, operation: str, key: CacheKey, error: redis_exc.RedisError
    ) -> None:
        logger.warning(
            "[Cache] %s failed for %s: %s", operation, key, describe_error(error)
        )
        self._reporter.report_degraded(error)
