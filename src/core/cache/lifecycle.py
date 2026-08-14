from fastapi import FastAPI
from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.decorators import validate_declared_ttls
from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.main.config import config

logger = get_logger(__name__)


async def on_cache_startup(app: FastAPI) -> None:
    redis_client: Redis | None = getattr(app.state, "redis_client", None)
    if redis_client is None:
        raise RuntimeError(
            "Redis client is not initialized. Start the cache after on_redis_startup."
        )

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
