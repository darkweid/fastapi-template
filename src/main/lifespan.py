from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from loggers import get_logger
from src.core.cache.lifecycle import (
    on_cache_shutdown,
    on_cache_startup,
    open_cache_redis_client,
)
from src.core.http.client import close_http_clients
from src.core.limiter import FastAPILimiter
from src.core.redis.core import create_redis_client
from src.core.redis.lifecycle import verify_redis_client
from src.core.storage.s3.dependencies import build_s3_adapter
from src.main.config import config
from src.main.sentry import init_sentry
from taskiq_worker.broker import broker

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    init_sentry()

    # Each close is pushed right before its resource starts, and every close is
    # safe on a resource that never finished starting. A failure anywhere in
    # startup, or an exception out of the app, therefore closes whatever was
    # opened, in reverse: dependents first, the shared Redis client last.
    async with AsyncExitStack() as stack:
        redis_client = create_redis_client(connection_url=config.redis.dsn)
        stack.push_async_callback(redis_client.aclose)
        await verify_redis_client(redis_client)
        app.state.redis_client = redis_client

        # The limiter reuses app.state.redis_client, and so does the cache unless
        # CACHE_REDIS_URL moves it to an instance of its own; sessions and the
        # health probe always stay on the application client.
        stack.push_async_callback(FastAPILimiter.close)
        await FastAPILimiter.init(redis_client)
        cache_client = await open_cache_redis_client(stack, redis_client)
        stack.push_async_callback(on_cache_shutdown)
        await on_cache_startup(cache_client)

        # Kicker-side broker init: .kiq() requires a started broker. The worker
        # CLI targets taskiq_worker.app:broker directly and starts/stops the
        # broker itself, so this guard keeps that startup/shutdown pair scoped
        # to the FastAPI process only. Pushed before startup: startup can fail
        # after it has borrowed a pooled connection (XGROUP CREATE), and every
        # step of shutdown is a no-op on a broker that never started.
        if not broker.is_worker_process:
            stack.push_async_callback(broker.shutdown)
            await broker.startup()

        stack.push_async_callback(close_http_clients)
        # Built once per process and reused across requests instead of opening a
        # fresh aioboto3 client per call; get_s3_adapter reads it off app.state.
        # Absent entirely when disabled, so a misconfigured deploy fails at the
        # first S3 call rather than at startup.
        if config.s3.S3_ENABLED:
            app.state.s3_adapter = await stack.enter_async_context(
                build_s3_adapter(config.s3)
            )

        yield
