"""Worker entrypoint: `taskiq worker taskiq_worker.app:broker`.

Every task module must be imported here - a task module not imported here is
invisible to the worker.
"""

from taskiq import TaskiqEvents, TaskiqState

import src.core.email_service.tasks  # noqa: F401
import src.core.outbox.tasks  # noqa: F401
from src.main.sentry import init_sentry
import src.user.auth.tasks  # noqa: F401
import src.user.tasks  # noqa: F401
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import close_tasks_redis_client

init_sentry()


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(_: TaskiqState) -> None:
    await close_tasks_redis_client()


__all__ = ["broker"]
