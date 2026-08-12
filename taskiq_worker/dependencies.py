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


async def get_tasks_redis_client() -> AsyncGenerator[Redis]:
    """Per-task-run Redis client (app DB 0), closed after the task finishes."""
    client = create_redis_client(config.redis.dsn)
    try:
        yield client
    finally:
        await client.aclose()
