from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from src.core.redis.cache.lifecycle import (
    on_redis_cache_shutdown,
    on_redis_cache_startup,
)
from src.core.redis.lifecycle import on_redis_shutdown, on_redis_startup
from src.main.config import config
from src.main.sentry import init_sentry
from taskiq_worker.broker import broker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    init_sentry()
    await on_redis_startup(app, config.redis.dsn)

    await on_redis_cache_startup()

    # Kicker-side broker init: .kiq() requires a started broker. The guard
    # keeps the worker process (which starts the broker itself) from a double
    # startup when it imports the app.
    if not broker.is_worker_process:
        await broker.startup()

    yield

    if not broker.is_worker_process:
        await broker.shutdown()
    await on_redis_cache_shutdown()
    await on_redis_shutdown(app)
