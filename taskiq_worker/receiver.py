"""Worker receiver with at-least-once dedup.

Wired via CLI: `taskiq worker ... --receiver taskiq_worker.receiver:IdempotencyReceiver`.
A middleware cannot do this job: an exception raised in `pre_execute` is not
caught by `Receiver.callback`, so the message would never be acked and the
broker would redeliver it forever.
"""

from collections.abc import Callable
from typing import Any

from taskiq.message import TaskiqMessage
from taskiq.receiver import Receiver
from taskiq.result import TaskiqResult

from loggers import get_logger
from src.core.redis.core import create_redis_client
from src.main.config import config

logger = get_logger(__name__)

IDEMPOTENCY_MARKER_TTL_SECONDS = 3600


def build_idempotency_marker_key(task_id: str) -> str:
    return f"taskiq:done:{task_id}"


class IdempotencyReceiver(Receiver):
    """Skip tasks whose completion marker is already in Redis.

    The marker is written after a successful run but before the result is
    saved and the message acked, so a worker crash between the side effect and
    XACK no longer causes a duplicate execution on reclaim. Dedup is
    best-effort: on any Redis error the receiver fails open and executes -
    the baseline semantics stay at-least-once.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._marker_client = create_redis_client(config.redis.tasks_dsn)

    async def run_task(
        self, target: Callable[..., Any], message: TaskiqMessage
    ) -> TaskiqResult[Any]:
        marker_key = build_idempotency_marker_key(message.task_id)
        try:
            already_done = await self._marker_client.get(marker_key)
        except Exception:
            logger.warning(
                "Idempotency marker check failed for task %s; executing anyway",
                message.task_id,
            )
            already_done = None
        if already_done is not None:
            logger.debug(
                "Task %s already completed, skipping duplicate delivery",
                message.task_id,
            )
            return TaskiqResult(is_err=False, return_value=None, execution_time=0.0)

        result = await super().run_task(target, message)

        if not result.is_err:
            try:
                await self._marker_client.set(
                    marker_key, "1", ex=IDEMPOTENCY_MARKER_TTL_SECONDS
                )
            except Exception:
                logger.warning(
                    "Failed to set idempotency marker for task %s", message.task_id
                )
        return result
