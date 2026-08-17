"""Worker entrypoint: `taskiq worker taskiq_worker.app:broker`.

Every task module must be imported here - a task module not imported here is
invisible to the worker.
"""

from taskiq import TaskiqEvents, TaskiqState

from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
import src.core.email_service.tasks  # noqa: F401
import src.core.outbox.tasks  # noqa: F401
from src.main.config import config
from src.main.sentry import init_sentry
import src.user.auth.tasks  # noqa: F401
import src.user.tasks  # noqa: F401
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import (
    close_tasks_redis_client,
    get_tasks_redis_singleton,
)

init_sentry()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_worker_startup(_: TaskiqState) -> None:
    # Mirror of the API's `on_cache_startup` (src/core/cache/lifecycle.py) so
    # tasks can read and invalidate the same cache the API writes. Same prefix
    # and TTLs are what make the keys shared; the client is the worker's own.
    set_cache(
        RedisCache(
            redis_client=get_tasks_redis_singleton(),
            serializer=JsonSerializer(),
            prefix=config.cache.CACHE_KEY_PREFIX,
            default_ttl=config.cache.CACHE_DEFAULT_TTL,
            version_ttl=config.cache.CACHE_VERSION_TTL,
            enabled=config.cache.CACHE_ENABLED,
        )
    )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(_: TaskiqState) -> None:
    reset_cache()
    await close_tasks_redis_client()


__all__ = ["broker"]
