from fastapi import FastAPI

from loggers import get_logger
from src.core.redis.core import create_redis_client

logger = get_logger(__name__)


async def on_redis_startup(app: FastAPI, connection_url: str) -> None:
    """
    Initialize a Redis client and attach it to app.state for DI access.

    Pings before publishing the client: an unreachable Redis must fail startup
    rather than surface later as a per-request error.
    """
    redis_client = create_redis_client(connection_url=connection_url)
    if not await redis_client.ping():
        raise RuntimeError("Redis ping failed during startup")
    app.state.redis_client = redis_client
    logger.info("Redis client created successfully.")


async def on_redis_shutdown(app: FastAPI) -> None:
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client:
        logger.info("Closing Redis client...")
        await redis_client.aclose()
        logger.info("Redis client closed.")
