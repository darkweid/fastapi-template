from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import tasks_async_session
from src.core.redis.core import create_redis_client
from src.main.config import config


async def get_tasks_session() -> AsyncGenerator[AsyncSession]:
    """Per-task-run DB session on the worker's isolated engine pool."""
    async with tasks_async_session() as session:
        yield session


_tasks_redis_client: Redis | None = None


def get_tasks_redis_singleton() -> Redis:
    """One Redis client per worker process; closed by the broker shutdown hook."""
    global _tasks_redis_client
    if _tasks_redis_client is None:
        _tasks_redis_client = create_redis_client(config.redis.dsn)
    return _tasks_redis_client


async def get_tasks_redis_client() -> AsyncGenerator[Redis]:
    yield get_tasks_redis_singleton()


async def close_tasks_redis_client() -> None:
    global _tasks_redis_client
    if _tasks_redis_client is not None:
        await _tasks_redis_client.aclose()
        _tasks_redis_client = None


_cache_redis_client: Redis | None = None


def get_cache_redis_singleton() -> Redis:
    """
    The tasks client, or a client of its own when CACHE_REDIS_URL moves the
    cache, so task-side invalidations reach the instance the API caches on.
    """
    global _cache_redis_client
    url = config.cache.dedicated_redis_url
    if url is None:
        return get_tasks_redis_singleton()
    if _cache_redis_client is None:
        _cache_redis_client = create_redis_client(connection_url=url)
    return _cache_redis_client


async def close_cache_redis_client() -> None:
    """Close the dedicated cache client; a borrowed tasks client is left alone."""
    global _cache_redis_client
    if _cache_redis_client is not None:
        await _cache_redis_client.aclose()
        _cache_redis_client = None
