from contextlib import AsyncExitStack

from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.decorators import validate_declared_ttls
from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.core.redis.core import create_redis_client
from src.core.redis.lifecycle import verify_redis_client
from src.main.config import config

logger = get_logger(__name__)


async def open_cache_redis_client(
    stack: AsyncExitStack, application_client: Redis
) -> Redis:
    """
    The client the cache runs on.

    Blank `CACHE_REDIS_URL` keeps the cache on the application client. A set one
    gets a client of its own whose close is on `stack` before the ping, so a wrong
    URL fails startup instead of every cache call, and leaks no pool doing so.
    """
    url = config.cache.CACHE_REDIS_URL.get_secret_value()
    if not url:
        return application_client
    client = create_redis_client(connection_url=url)
    stack.push_async_callback(client.aclose)
    await verify_redis_client(client)
    logger.info("Cache runs on its own Redis instance.")
    return client


async def on_cache_startup(redis_client: Redis) -> None:
    # Routers are imported by now, so every @cached / @cached_route ttl in the
    # application is known and a bad one fails startup instead of every request.
    validate_declared_ttls(config.cache.CACHE_VERSION_TTL)

    set_cache(
        RedisCache(
            redis_client=redis_client,
            serializer=JsonSerializer(),
            prefix=config.cache.CACHE_KEY_PREFIX,
            default_ttl=config.cache.CACHE_DEFAULT_TTL,
            version_ttl=config.cache.CACHE_VERSION_TTL,
            enabled=config.cache.CACHE_ENABLED,
        )
    )
    logger.info("Cache started (enabled=%s).", config.cache.CACHE_ENABLED)


async def on_cache_shutdown() -> None:
    reset_cache()
    logger.info("Cache stopped.")
