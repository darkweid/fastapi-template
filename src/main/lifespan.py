from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from loggers import get_logger
from src.core.cache.lifecycle import on_cache_shutdown, on_cache_startup
from src.core.limiter import FastAPILimiter
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

    # Limiter and cache both reuse app.state.redis_client, so the process opens
    # exactly one Redis connection pool.
    await FastAPILimiter.init(app.state.redis_client)
    await on_cache_startup(app)

    # Kicker-side broker init: .kiq() requires a started broker. The worker
    # CLI targets taskiq_worker.app:broker directly and starts/stops the
    # broker itself, so this guard keeps that startup/shutdown pair scoped to
    # the FastAPI process only.
    if not broker.is_worker_process:
        await broker.startup()

    async with AsyncExitStack() as stack:
        # Built once per process and reused across requests instead of opening a
        # fresh aioboto3 client per call; get_s3_adapter reads it off app.state.
        # Absent entirely when disabled, so a misconfigured deploy fails at the
        # first S3 call rather than at startup.
        if config.s3.S3_ENABLED:
            app.state.s3_adapter = await stack.enter_async_context(
                build_s3_adapter(config.s3)
            )

        yield

    if not broker.is_worker_process:
        await broker.shutdown()
    await on_cache_shutdown()
    await FastAPILimiter.close()
    await on_redis_shutdown(app)
