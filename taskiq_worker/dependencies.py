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


def _tasks_redis() -> Redis:
    global _tasks_redis_client
    if _tasks_redis_client is None:
        _tasks_redis_client = create_redis_client(config.redis.dsn)
    return _tasks_redis_client


async def get_tasks_redis_client() -> AsyncGenerator[Redis]:
    # One client per worker process; closed by the broker shutdown hook.
    yield _tasks_redis()


async def close_tasks_redis_client() -> None:
    global _tasks_redis_client
    if _tasks_redis_client is not None:
        await _tasks_redis_client.aclose()
        _tasks_redis_client = None
