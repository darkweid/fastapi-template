from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from loggers import get_logger
from src.core.cache.lifecycle import on_cache_shutdown, on_cache_startup
from src.core.limiter.lifecycle import on_limiter_shutdown, on_limiter_startup
from src.core.redis.lifecycle import on_redis_shutdown, on_redis_startup
from src.core.storage.s3.dependencies import build_s3_adapter
from src.main.config import config
from src.main.sentry import init_sentry
from taskiq_worker.broker import broker

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    init_sentry()
    await on_redis_startup(app, config.redis.dsn)
    await on_limiter_startup(config.redis.dsn)

    # Cache starts after on_redis_startup because it reuses app.state.redis_client -
    # there must be no second Redis connection.
    await on_cache_startup(app)

    # Kicker-side broker init: .kiq() requires a started broker. The worker
    # CLI targets taskiq_worker.app:broker directly and starts/stops the
    # broker itself, so this guard keeps that startup/shutdown pair scoped to
    # the FastAPI process only.
    if not broker.is_worker_process:
        await broker.startup()

    # Built once per process and reused across requests instead of opening a
    # fresh aioboto3 client per call; get_s3_adapter reads it off app.state.
    # Absent entirely when disabled, so a misconfigured deploy fails at the
    # first S3 call rather than at startup.
    if config.s3.S3_ENABLED:
        s3_adapter = build_s3_adapter(config.s3)
        await s3_adapter.__aenter__()
        app.state.s3_adapter = s3_adapter

    yield

    if config.s3.S3_ENABLED:
        await app.state.s3_adapter.__aexit__(None, None, None)

    if not broker.is_worker_process:
        await broker.shutdown()
    await on_cache_shutdown()
    await on_limiter_shutdown()
    await on_redis_shutdown(app)
