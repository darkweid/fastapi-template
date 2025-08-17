import logging

from src.core.limiter import FastAPILimiter
from src.core.redis.cache.backend.redis_backend import RedisCacheBackend
from src.main.config import config

logger = logging.getLogger("redis")


async def on_redis_cache_startup() -> None:
    await FastAPILimiter.init(config.redis.dsn)
    await RedisCacheBackend().connect(config.redis.dsn)
    logger.info("Redis cache started successfully.")


async def on_redis_cache_shutdown() -> None:
    logger.info("Redis cache shutting down...")
    await RedisCacheBackend().close()
    await FastAPILimiter.close()
