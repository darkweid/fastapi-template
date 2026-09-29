"""Worker receiver with at-least-once dedup.

Wired via CLI: `taskiq worker ... --receiver taskiq_worker.receiver:IdempotencyReceiver`.
A middleware cannot do this job: an exception raised in `pre_execute` is not
caught by `Receiver.callback`, so the message would never be acked and the
broker would redeliver it forever.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from taskiq.acks import AckController
from taskiq.message import TaskiqMessage
from taskiq.receiver import Receiver
from taskiq.result import TaskiqResult

from loggers import get_logger
from src.core.redis.core import create_redis_client
from src.main.config import config

logger = get_logger(__name__)

IDEMPOTENCY_MARKER_TTL_SECONDS = 3600

# The claim is short-lived and renewed every CLAIM_RENEW_INTERVAL_SECONDS while
# its run is alive, so a task of any length keeps it and a crashed worker's claim
# lapses within one TTL. It stays well below the stream's reclaim timeout: a
# message reclaimed from a crashed worker must find the claim gone, or it is
# skipped, acked and lost.
RUNNING_CLAIM_TTL_SECONDS = 60
CLAIM_RENEW_INTERVAL_SECONDS = 20

RELEASE_CLAIM_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

RENEW_CLAIM_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


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
    after-commit publish already went out. The claim is renewed while the run
    lasts, and a failed run releases it so a retry can take it. Dedup is best-effort: on any Redis error the receiver
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
        claim_token = await self._claim(message.task_id)
        if claim_token is None:
            logger.debug(
                "Task %s is running elsewhere, skipping duplicate delivery",
                message.task_id,
            )
            _withhold_ack(ack_controller)
            return _skipped()
        if await self._is_done(message.task_id):
            logger.debug(
                "Task %s already completed, skipping duplicate delivery",
                message.task_id,
            )
            await self._release_claim(message.task_id, claim_token)
            return _skipped()

        heartbeat = asyncio.create_task(self._keep_claim(message.task_id, claim_token))
        try:
            result = await super().run_task(target, message, ack_controller)
            if not result.is_err:
                await self._mark_done(message.task_id)
        finally:
            heartbeat.cancel()
            # After the marker, never before: in between, a duplicate would
            # find neither and run.
            await self._release_claim(message.task_id, claim_token)
        return result

    async def _keep_claim(self, task_id: str, token: str) -> None:
        """Renew the claim for as long as the run lasts; cancelled when it ends.

        A renewal that fails is logged and the next one tried: the claim lapses
        only if renewals keep failing for a whole TTL.
        """
        while True:
            await asyncio.sleep(CLAIM_RENEW_INTERVAL_SECONDS)
            try:
                await cast(
                    Awaitable[int],
                    self._marker_client.eval(
                        RENEW_CLAIM_SCRIPT,
                        1,
                        build_running_claim_key(task_id),
                        token,
                        str(RUNNING_CLAIM_TTL_SECONDS),
                    ),
                )
            except Exception:
                logger.warning("Failed to renew running claim for task %s", task_id)

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

    async def _claim(self, task_id: str) -> str | None:
        """Answer the token this run holds the claim under, or None when another
        delivery holds it. A Redis error fails open with a token nobody stored."""
        token = str(uuid4())
        try:
            acquired = await self._marker_client.set(
                build_running_claim_key(task_id),
                token,
                ex=RUNNING_CLAIM_TTL_SECONDS,
                nx=True,
            )
        except Exception:
            logger.warning(
                "Running claim failed for task %s; executing anyway", task_id
            )
            return token
        return token if acquired else None

    async def _mark_done(self, task_id: str) -> None:
        try:
            await self._marker_client.set(
                build_idempotency_marker_key(task_id),
                "1",
                ex=IDEMPOTENCY_MARKER_TTL_SECONDS,
            )
        except Exception:
            logger.warning("Failed to set idempotency marker for task %s", task_id)

    async def _release_claim(self, task_id: str, token: str) -> None:
        # Compare and delete in one step: a run that outlived its claim must not
        # delete the claim a reclaimed delivery took after it expired.
        try:
            await cast(
                Awaitable[int],
                self._marker_client.eval(
                    RELEASE_CLAIM_SCRIPT, 1, build_running_claim_key(task_id), token
                ),
            )
        except Exception:
            # The claim still expires on its own; a retry arriving before that
            # is skipped, which is why this is logged as a warning.
            logger.warning("Failed to release running claim for task %s", task_id)


def _withhold_ack(ack_controller: AckController | None) -> None:
    """Leave a delivery that found a live claim pending in the stream.

    It may be the stream reclaiming the very entry the claim holder is still
    running; acked here, the entry would be gone if that worker then crashed.
    Left pending, it is reclaimed again later and then finds either the done
    marker or a lapsed claim. The holder's own ack settles the entry, since
    XACK is per group, not per consumer. Marking the controller acked is the
    only way to stop `Receiver.callback` from acking it after this returns.
    """
    if ack_controller is not None:
        ack_controller.is_acked = True


def _skipped() -> TaskiqResult[Any]:
    return TaskiqResult(is_err=False, return_value=None, execution_time=0.0)
