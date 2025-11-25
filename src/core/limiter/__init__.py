from collections.abc import Awaitable, Callable
from math import ceil

from fastapi import HTTPException, Request, Response, status
import redis.asyncio as aredis

from loggers import get_logger
from src.core.limiter.script import lua_script
from src.main.config import config

logger = get_logger(__name__)


async def default_identifier(request: Request) -> str:
    """
    Creates a rate-limiting key based on the IP address and request path.
    """
    if config.app.TRUST_PROXY_HEADERS:
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
    else:
        ip = request.client.host if request.client else "unknown"

    return f"{ip}:{request.scope['path']}"


async def http_default_callback(
    request: Request, response: Response, pexpire: int
) -> None:
    """
    Default callback for rate-limited responses. Raises 429 with a Retry-After header.
    """
    expire_seconds = ceil(pexpire / 1000)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too Many Requests",
        headers={"Retry-After": str(expire_seconds)},
    )


class FastAPILimiter:
    """
    Singleton-style rate limiter setup using Redis Lua scripting.
    Should be initialized once at application startup.
    """

    redis: aredis.Redis | None = None
    prefix: str = "limiter"
    lua_sha: str | None = None
    identifier: Callable[[Request], Awaitable[str]] = default_identifier
    http_callback: Callable[[Request, Response, int], Awaitable[None]] = (
        http_default_callback
    )
    lua_script: str = lua_script

    @classmethod
    async def init(
        cls,
        redis_client: aredis.Redis | str,
        prefix: str | None = None,
        identifier: Callable[[Request], Awaitable[str]] | None = None,
        http_callback: None | (
            Callable[[Request, Response, int], Awaitable[None]]
        ) = None,
    ) -> None:
        """
        Initializes a Redis client, identifier and callback.
        Loads a Lua script to Redis and stores SHA for evalsha.
        """
        logger.debug("Initializing FastAPILimiter...")

        redis_instance: aredis.Redis
        if isinstance(redis_client, str):
            # Type ignored since the function is known to return Redis despite missing type stub
            redis_instance = aredis.from_url(redis_client)  # type: ignore
        else:
            redis_instance = redis_client

        cls.redis = redis_instance
        cls.prefix = prefix or cls.prefix
        cls.identifier = identifier or cls.identifier
        cls.http_callback = http_callback or cls.http_callback

        try:
            cls.lua_sha = await redis_instance.script_load(cls.lua_script)
        except Exception as e:
            logger.error(f"Failed to load Lua script: {e}")
            raise RuntimeError(f"Failed to load Lua script: {e}")

        logger.info("FastAPILimiter initialized successfully.")

    @classmethod
    async def close(cls) -> None:
        """
        Properly closes Redis connection and clears state.
        """
        if cls.redis:
            await cls.redis.aclose()
        cls.redis = None
        cls.lua_sha = None

    @classmethod
    def is_initialized(cls) -> bool:
        """
        Returns whether the limiter was properly initialized.
        """
        return cls.redis is not None and cls.lua_sha is not None
