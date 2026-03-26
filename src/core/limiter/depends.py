from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import Lock
import time
from typing import Annotated, Any, ClassVar, cast

from fastapi import Request, Response
from pydantic import Field
import redis.exceptions as redisExc
import sentry_sdk

from loggers import get_logger
from src.core.limiter import FastAPILimiter

logger = get_logger(__name__)


def _current_time_ms() -> int:
    return int(time.monotonic() * 1000)


@dataclass(slots=True)
class _InMemoryRateLimitWindow:
    count: int
    expires_at_ms: int


class RateLimiter:
    """
    HTTP rate limiter dependency for FastAPI endpoints.
    Applies rate-limiting logic via Redis and Lua scripting.
    """

    _fallback_windows: ClassVar[dict[str, _InMemoryRateLimitWindow]] = {}
    _fallback_lock: ClassVar[Lock] = Lock()
    _fallback_state_lock: ClassVar[Lock] = Lock()
    _fallback_sentry_cooldown_ms: ClassVar[int] = 5 * 60_000
    _fallback_max_entries: ClassVar[int] = (
        100_000  # Around 20-25 MB max for a fallback state
    )
    _redis_degraded_since_ms: ClassVar[int | None] = None
    _last_redis_degraded_report_ms: ClassVar[int | None] = None

    def __init__(
        self,
        times: Annotated[int, Field(ge=1)] = 1,
        milliseconds: Annotated[int, Field(ge=0)] = 0,
        seconds: Annotated[int, Field(ge=0)] = 0,
        minutes: Annotated[int, Field(ge=0)] = 0,
        hours: Annotated[int, Field(ge=0)] = 0,
        identifier: Callable[[Request], Awaitable[str]] = FastAPILimiter.identifier,
        callback: Callable[
            [Request, Response, int], Awaitable[None]
        ] = FastAPILimiter.http_callback,
    ) -> None:
        """
        Initialize the rate limiter with time windows and custom behaviors.

        Args:
            times: Number of allowed requests in the time window
            milliseconds/seconds/minutes/hours: Time window duration
            identifier: Async function to generate unique rate-limit key
            callback: Async function to call when limit exceeded
        """
        if FastAPILimiter.identifier is None or FastAPILimiter.http_callback is None:
            raise RuntimeError("FastAPILimiter must be initialized before use.")

        self.times = times
        self.milliseconds = (
            milliseconds + 1000 * seconds + 60_000 * minutes + 3_600_000 * hours
        )

        if self.milliseconds <= 0:
            raise ValueError("Rate limiter window must be greater than 0ms.")

        self.identifier = identifier
        self.callback = callback

    async def _eval_redis_limit(self, key: str) -> int:
        redis = FastAPILimiter.redis
        if redis is None:
            raise RuntimeError("Redis is not connected.")

        lua_sha = FastAPILimiter.lua_sha
        if lua_sha is None:
            raise RuntimeError("Lua script SHA is not initialized.")

        try:
            eval_result = redis.evalsha(
                lua_sha,
                1,
                key,
                str(self.times),
                str(self.milliseconds),
            )
            result = await cast(Awaitable[Any], eval_result)
            return int(result)
        except redisExc.NoScriptError:
            script_load_result = redis.script_load(FastAPILimiter.lua_script)
            script_result = await cast(Awaitable[str], script_load_result)
            FastAPILimiter.lua_sha = script_result

            reloaded_lua_sha = FastAPILimiter.lua_sha
            if reloaded_lua_sha is None:
                raise RuntimeError("Failed to load Lua script.")

            eval_result = redis.evalsha(
                reloaded_lua_sha,
                1,
                key,
                str(self.times),
                str(self.milliseconds),
            )
            result = await cast(Awaitable[Any], eval_result)
            return int(result)

    @classmethod
    def _mark_redis_degraded(cls, now_ms: int) -> bool:
        with cls._fallback_state_lock:
            if cls._redis_degraded_since_ms is None:
                cls._redis_degraded_since_ms = now_ms
                cls._last_redis_degraded_report_ms = now_ms
                return True

            last_report_ms = cls._last_redis_degraded_report_ms
            if (
                last_report_ms is None
                or now_ms - last_report_ms >= cls._fallback_sentry_cooldown_ms
            ):
                cls._last_redis_degraded_report_ms = now_ms
                return True

            return False

    @classmethod
    def _mark_redis_recovered(cls, now_ms: int) -> int | None:
        with cls._fallback_state_lock:
            degraded_since_ms = cls._redis_degraded_since_ms
            if degraded_since_ms is None:
                return None

            cls._redis_degraded_since_ms = None
            cls._last_redis_degraded_report_ms = None
            return now_ms - degraded_since_ms

    def _report_redis_fallback_to_sentry(
        self, redis_error: redisExc.RedisError
    ) -> None:
        now_ms = _current_time_ms()
        if not self._mark_redis_degraded(now_ms):
            return

        sentry_sdk.capture_message(
            "[RateLimiter] Redis is unavailable. In-memory fallback limiter is active. "
            f"Error: {type(redis_error).__name__}: {redis_error}",
            level="error",
        )

    def _report_redis_recovery_to_sentry(self) -> None:
        recovered_after_ms = self._mark_redis_recovered(_current_time_ms())
        if recovered_after_ms is None:
            return

        sentry_sdk.capture_message(
            "[RateLimiter] Redis limiter recovered. "
            f"In-memory fallback limiter is no longer active. Downtime: {recovered_after_ms}ms.",
            level="info",
        )

    @classmethod
    def _prune_expired_fallback_windows(cls, now_ms: int) -> None:
        expired_keys = [
            key
            for key, state in cls._fallback_windows.items()
            if state.expires_at_ms <= now_ms
        ]
        for expired_key in expired_keys:
            cls._fallback_windows.pop(expired_key, None)

    @classmethod
    def _evict_oldest_fallback_window(cls) -> str | None:
        if not cls._fallback_windows:
            return None

        oldest_key = min(
            cls._fallback_windows,
            key=lambda key: cls._fallback_windows[key].expires_at_ms,
        )
        cls._fallback_windows.pop(oldest_key, None)
        return oldest_key

    def _check_limit_in_memory(self, key: str) -> int:
        now_ms = _current_time_ms()
        with self._fallback_lock:
            self._prune_expired_fallback_windows(now_ms)
            state = self._fallback_windows.get(key)

            if state is None:
                if len(self._fallback_windows) >= self._fallback_max_entries:
                    evicted_key = self._evict_oldest_fallback_window()
                    logger.warning(
                        "[RateLimiter] In-memory fallback capacity reached (%s). "
                        "Evicting oldest key %s to keep fallback state bounded.",
                        self._fallback_max_entries,
                        evicted_key,
                    )
                self._fallback_windows[key] = _InMemoryRateLimitWindow(
                    count=1,
                    expires_at_ms=now_ms + self.milliseconds,
                )
                return 0

            if state.count + 1 > self.times:
                return max(state.expires_at_ms - now_ms, 1)

            state.count += 1
            return 0

    def _check_limit_with_fallback(
        self, key: str, redis_error: redisExc.RedisError
    ) -> int:
        logger.error(
            "[RateLimiter] Redis unavailable for key %s: %s. Using in-memory fallback limiter.",
            key,
            redis_error,
        )
        self._report_redis_fallback_to_sentry(redis_error)
        try:
            return self._check_limit_in_memory(key)
        except Exception:
            logger.error(
                "[RateLimiter] Redis unavailable for key %s and in-memory fallback failed. Allowing request as a security-significant incident.",
                key,
                exc_info=True,
            )
            return 0

    async def _check_limit(self, key: str) -> int:
        try:
            result = await self._eval_redis_limit(key)
            self._report_redis_recovery_to_sentry()
            return result
        except (redisExc.ConnectionError, redisExc.RedisError) as exc:
            return self._check_limit_with_fallback(key, exc)

    async def __call__(self, request: Request, response: Response) -> None:
        """
        FastAPI-compatible call method that applies rate limiting.

        Raises:
            HTTPException 429 if the limit is exceeded
        """
        if not FastAPILimiter.is_initialized():
            raise RuntimeError("FastAPILimiter must be initialized before use.")

        rate_key = await self.identifier(request)
        endpoint_name = request.scope["endpoint"].__name__
        key = f"{FastAPILimiter.prefix}:{rate_key}:{endpoint_name}"
        logger.debug(f"RateLimiter key: {key}")
        pexpire = await self._check_limit(key)

        if pexpire != 0:
            logger.warning(
                f"[RateLimiter] Limit exceeded for key: {key}, will retry after {pexpire}ms"
            )
            await self.callback(request, response, pexpire)

        # Explicitly return None to ensure FastAPI continues to the next dependency
        return None
