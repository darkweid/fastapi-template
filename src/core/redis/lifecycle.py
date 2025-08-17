import logging

from fastapi import FastAPI

from src.core.redis.core import create_redis_pool

logger = logging.getLogger("redis")


async def on_redis_startup(app: FastAPI, connection_url):
    redis_pool = create_redis_pool(connection_url=connection_url)

    app.state.redis_pool = redis_pool

    logger.info("Redis pool created successfully.")


async def on_redis_shutdown(app: FastAPI):
    pool_to_close = getattr(app.state, "redis_pool", None)

    if pool_to_close:
        logger.info("Clothing Redis pool...")
        await pool_to_close.close()
        logger.info("Redis pool is closed.")
