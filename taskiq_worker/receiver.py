"""Worker receiver with at-least-once dedup.

Wired via CLI: `taskiq worker ... --receiver taskiq_worker.receiver:IdempotencyReceiver`.
A middleware cannot do this job: an exception raised in `pre_execute` is not
caught by `Receiver.callback`, so the message would never be acked and the
broker would redeliver it forever.
"""

from collections.abc import Callable
from typing import Any

from taskiq.acks import AckController
from taskiq.message import TaskiqMessage
from taskiq.receiver import Receiver
from taskiq.result import TaskiqResult

from loggers import get_logger
from src.core.redis.core import create_redis_client
from src.main.config import config
from taskiq_worker.broker import STREAM_IDLE_TIMEOUT_SECONDS

logger = get_logger(__name__)

IDEMPOTENCY_MARKER_TTL_SECONDS = 3600

# A run holds its claim at most this long. It stays below the stream's reclaim
# timeout: a message reclaimed from a crashed worker must find the claim gone,
# or it is skipped, acked and lost. A task running past the reclaim timeout is
# redelivered anyway, so a longer claim would buy nothing.
RUNNING_CLAIM_TTL_SECONDS = STREAM_IDLE_TIMEOUT_SECONDS - 60


def build_idempotency_marker_key(task_id: str) -> str:
    return f"taskiq:done:{task_id}"


def build_running_claim_key(task_id: str) -> str:
    return f"taskiq:running:{task_id}"


class IdempotencyReceiver(Receiver):
    """Skip a delivery whose task already completed or is running right now.

    The completion marker is written after a successful run but before the
    result is saved and the message acked, so a worker crash between the side
    effect and XACK no longer causes a duplicate execution on reclaim. The
    running claim covers the other duplicate: two deliveries of one task id in
    flight at once, as when the outbox sweeper republishes a row whose
    after-commit publish already went out. A failed run releases its claim so a
    retry can take it. Dedup is best-effort: on any Redis error the receiver
    fails open and executes - the baseline semantics stay at-least-once.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._marker_client = create_redis_client(config.redis.tasks_dsn)

    async def run_task(
        self,
        target: Callable[..., Any],
        message: TaskiqMessage,
        ack_controller: AckController | None = None,
    ) -> TaskiqResult[Any]:
        # Claim first, then read the marker. Read first, a run finishing in
        # between would write its marker and release its claim, and this
        # delivery would take the free claim and run again. A run releases only
        # after its marker is written, so a claim that is free is one whose
        # marker, if any, is already readable.
        if not await self._claim(message.task_id):
            logger.debug(
                "Task %s is running elsewhere, skipping duplicate delivery",
                message.task_id,
            )
            return _skipped()
        if await self._is_done(message.task_id):
            logger.debug(
                "Task %s already completed, skipping duplicate delivery",
                message.task_id,
            )
            await self._release_claim(message.task_id)
            return _skipped()

        try:
            result = await super().run_task(target, message, ack_controller)
            if not result.is_err:
                await self._mark_done(message.task_id)
        finally:
            # After the marker, never before: in between, a duplicate would
            # find neither and run.
            await self._release_claim(message.task_id)
        return result

    async def _is_done(self, task_id: str) -> bool:
        try:
            marker = await self._marker_client.get(
                build_idempotency_marker_key(task_id)
            )
        except Exception:
            logger.warning(
                "Idempotency marker check failed for task %s; executing anyway",
                task_id,
            )
            return False
        return marker is not None

    async def _claim(self, task_id: str) -> bool:
        try:
            return bool(
                await self._marker_client.set(
                    build_running_claim_key(task_id),
                    "1",
                    ex=RUNNING_CLAIM_TTL_SECONDS,
                    nx=True,
                )
            )
        except Exception:
            logger.warning(
                "Running claim failed for task %s; executing anyway", task_id
            )
            return True

    async def _mark_done(self, task_id: str) -> None:
        try:
            await self._marker_client.set(
                build_idempotency_marker_key(task_id),
                "1",
                ex=IDEMPOTENCY_MARKER_TTL_SECONDS,
            )
        except Exception:
            logger.warning("Failed to set idempotency marker for task %s", task_id)

    async def _release_claim(self, task_id: str) -> None:
        try:
            await self._marker_client.delete(build_running_claim_key(task_id))
        except Exception:
            # The claim still expires on its own; a retry arriving before that
            # is skipped, which is why this is logged as a warning.
            logger.warning("Failed to release running claim for task %s", task_id)


def _skipped() -> TaskiqResult[Any]:
    return TaskiqResult(is_err=False, return_value=None, execution_time=0.0)
