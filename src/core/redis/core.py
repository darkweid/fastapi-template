import logging

from fastapi import FastAPI
from redis import asyncio as redis


logger = logging.getLogger(__name__)


def create_redis_pool(
    connection_url: str,
) -> redis.Redis:
    try:
        pool = redis.ConnectionPool.from_url(connection_url)
        return redis.Redis(connection_pool=pool)
    except Exception as e:
        logger.exception(f"An error occurred when trying to create a new pool: {e}")
        raise


def get_redis_pool(app: FastAPI) -> redis.Redis:
    pool = getattr(app.state, "redis_pool", None)

    if pool is None:
        raise RuntimeError("Redis Pool does not found in app.state")

    return redis.Redis(connection_pool=pool)
