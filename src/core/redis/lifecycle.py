import logging

from fastapi import FastAPI

from src.core.redis.core import create_redis_client

logger = logging.getLogger("redis")


async def on_redis_startup(app: FastAPI, connection_url: str) -> None:
    """
    Initialize a Redis client and attach it to app.state for DI access.
    """
    redis_client = create_redis_client(connection_url=connection_url)
    # Fail fast if connection is invalid
    await redis_client.ping()
    app.state.redis_client = redis_client
    logger.info("Redis client created successfully.")


async def on_redis_shutdown(app: FastAPI) -> None:
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client:
        logger.info("Closing Redis client...")
        await redis_client.aclose()
        logger.info("Redis client closed.")
