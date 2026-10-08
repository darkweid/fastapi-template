"""Worker entrypoint: `taskiq worker taskiq_worker.app:broker`.

Every task module must be imported here - a task module not imported here is
invisible to the worker.
"""

from contextlib import AsyncExitStack

from taskiq import TaskiqEvents, TaskiqState

from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
import src.core.email_service.tasks  # noqa: F401
from src.core.http.client import close_http_clients
import src.core.outbox.tasks  # noqa: F401
from src.core.redis.lifecycle import verify_redis_client
from src.main.config import config
from src.main.event_subscribers import register_event_subscribers
from src.main.sentry import init_sentry
import src.user.auth.tasks  # noqa: F401
import src.user.tasks  # noqa: F401
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import (
    close_cache_redis_client,
    close_tasks_redis_client,
    get_cache_redis_singleton,
)

init_sentry()
register_event_subscribers()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_worker_startup(_: TaskiqState) -> None:
    # Mirror of the API's `on_cache_startup` (src/core/cache/lifecycle.py) so
    # tasks can read and invalidate the same cache the API writes. Same prefix
    # and TTLs are what make the keys shared; the client is the worker's own, on
    # the cache instance when CACHE_REDIS_URL is set.
    cache_client = get_cache_redis_singleton()
    # Pinged like the API's, so a wrong URL fails the worker at startup rather
    # than turning every task-side invalidation into a silent no-op.
    if config.cache.dedicated_redis_url is not None:
        await verify_redis_client(cache_client)
    set_cache(
        RedisCache(
            redis_client=cache_client,
            serializer=JsonSerializer(),
            prefix=config.cache.CACHE_KEY_PREFIX,
            default_ttl=config.cache.CACHE_DEFAULT_TTL,
            version_ttl=config.cache.CACHE_VERSION_TTL,
            enabled=config.cache.CACHE_ENABLED,
        )
    )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(_: TaskiqState) -> None:
    # One failing close must not skip the rest. The stack runs them in reverse:
    # the cache built on the Redis client is dropped first, the client last.
    # A raised close still propagates into the broker's own shutdown, which then
    # skips its pool disconnect; the process exits right after, so that is fine.
    async with AsyncExitStack() as stack:
        stack.push_async_callback(close_tasks_redis_client)
        stack.push_async_callback(close_cache_redis_client)
        stack.push_async_callback(close_http_clients)
        stack.callback(reset_cache)


__all__ = ["broker"]
