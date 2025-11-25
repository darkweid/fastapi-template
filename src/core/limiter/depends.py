from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

from fastapi import Request, Response
from pydantic import Field
import redis.exceptions as redisExc

from loggers import get_logger
from src.core.limiter import FastAPILimiter

logger = get_logger(__name__)


class RateLimiter:
    """
    HTTP rate limiter dependency for FastAPI endpoints.
    Applies rate-limiting logic via Redis and Lua scripting.
    """

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

    async def _check_limit(self, key: str) -> int:
        redis = FastAPILimiter.redis
        if redis is None:
            raise RuntimeError("Redis is not connected.")

        try:
            lua_sha = FastAPILimiter.lua_sha
            if lua_sha is None:
                raise RuntimeError("Lua script SHA is not initialized.")

            # Use cast to ensure the type is compatible with await
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
            # Cast the result to Awaitable for type compatibility
            script_load_result = redis.script_load(FastAPILimiter.lua_script)
            script_result = await cast(Awaitable[str], script_load_result)
            FastAPILimiter.lua_sha = script_result

            lua_sha = FastAPILimiter.lua_sha
            if lua_sha is None:
                raise RuntimeError("Failed to load Lua script.")

            # Use cast to ensure the type is compatible with await
            eval_result = redis.evalsha(
                lua_sha,
                1,
                key,
                str(self.times),
                str(self.milliseconds),
            )
            result = await cast(Awaitable[Any], eval_result)
            return int(result)

        except (redisExc.ConnectionError, redisExc.RedisError) as e:
            # Soft fallback: log error and skip rate limit
            logger.error(f"[RateLimiter] Redis unavailable: {e}. Skipping rate limit.")
            return 0

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
