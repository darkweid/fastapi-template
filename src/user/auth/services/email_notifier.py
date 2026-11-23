from contextlib import suppress
from typing import Annotated, Any, NamedTuple
from uuid import uuid4

from fastapi import Depends
from redis.asyncio import Redis
from taskiq.decor import AsyncTaskiqDecoratedTask

from loggers import get_logger
from src.core.auth.challenges import delete_if_value
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.describe import scrub
from src.core.errors.exceptions import InstanceProcessingException
from src.core.outbox.dependencies import get_task_dispatcher
from src.core.outbox.dispatcher import TaskDispatcher
from src.core.redis.dependencies import get_redis_client
from src.core.utils.security import mask_email
from src.user.auth.tasks import (
    send_reset_password_email_task,
    send_verification_email_task,
)
from src.user.models import User

logger = get_logger(__name__)


class ThrottleSlot(NamedTuple):
    """One claimed send window. `owner` is what makes a release safe: the slot
    may have expired and been claimed by another send since."""

    key: str
    owner: str


class EmailNotifier:
    """
    Coordinates sending a one-time-token email (verification or password
    reset):
    - stores the email delivery in the outbox of the caller's transaction,
    - performs throttling through Redis.

    One instance is bound to one task/message pair via its constructor, so
    the same class backs both the verification and the reset-password flow.
    """

    def __init__(
        self,
        dispatcher: TaskDispatcher,
        redis_client: Redis,
        *,
        task: AsyncTaskiqDecoratedTask[Any, Any],
        throttle_message: str,
        log_label: str,
        throttle_ttl_sec: int = 60,
    ) -> None:
        self.dispatcher = dispatcher
        self.redis_client = redis_client
        self.task = task
        self.throttle_message = throttle_message
        self.log_label = log_label
        self.throttle_ttl_sec = throttle_ttl_sec

    async def _claim_throttle_slot(self, key: str) -> ThrottleSlot:
        """Take the throttle slot, or refuse the send.

        SET NX in one round trip: reading the key and then writing it lets two
        concurrent resends both find it absent and both send.
        """
        slot = ThrottleSlot(key, uuid4().hex)
        claimed = await self.redis_client.set(
            slot.key, slot.owner, ex=self.throttle_ttl_sec, nx=True
        )
        if not claimed:
            raise InstanceProcessingException(self.throttle_message)
        return slot

    async def release_throttle(self, slot: ThrottleSlot) -> None:
        """Best-effort throttle release for flows that failed after setting it.

        The throttle key outlives a rolled-back transaction (Redis is not part
        of it), so a failed flow must drop the key or the user stays locked out
        for the full TTL without any email queued.
        """
        with suppress(Exception):
            await delete_if_value(slot.key, slot.owner, self.redis_client)

    async def send(
        self,
        *,
        uow: ApplicationUnitOfWork,
        user: User,
        throttle_key: str | None = None,
    ) -> ThrottleSlot | None:
        """Queue the email and answer the throttle slot it claimed.

        A busy slot raises InstanceProcessingException and queues nothing. The
        caller releases the slot when anything after this fails, the commit
        included. Without a key nothing is claimed.
        """
        slot = await self._claim_throttle_slot(throttle_key) if throttle_key else None
        try:
            await self.dispatcher.enqueue_transactional(
                uow,
                self.task,
                user.email,
                user.full_name,
                throttle_key=slot.key if slot else None,
                throttle_owner=slot.owner if slot else None,
            )
        except Exception as error:
            # The outbox insert failed with the transaction still open: release
            # the throttle so the user can retry immediately.
            if slot:
                await self.release_throttle(slot)
            logger.error(
                "Failed to enqueue %s email for %s",
                self.log_label,
                mask_email(user.email),
                exc_info=scrub(error),
            )
            raise
        return slot


def get_verification_notifier(
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> EmailNotifier:
    return EmailNotifier(
        dispatcher=dispatcher,
        redis_client=redis_client,
        task=send_verification_email_task,
        throttle_message="We've already sent you a verification email.",
        log_label="verification",
    )


def get_reset_password_notifier(
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> EmailNotifier:
    return EmailNotifier(
        dispatcher=dispatcher,
        redis_client=redis_client,
        task=send_reset_password_email_task,
        throttle_message="We've already sent you a reset-password email.",
        log_label="password reset",
    )
