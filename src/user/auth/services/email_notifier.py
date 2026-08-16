from contextlib import suppress
from typing import Annotated, Any

from fastapi import Depends
from redis.asyncio import Redis
from taskiq.decor import AsyncTaskiqDecoratedTask

from loggers import get_logger
from src.core.database.uow import ApplicationUnitOfWork
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

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(self.throttle_message)
        await self.redis_client.setex(key, self.throttle_ttl_sec, "1")

    async def release_throttle(self, throttle_key: str) -> None:
        """Best-effort throttle release for flows that failed after setting it.

        The throttle key outlives a rolled-back transaction (Redis is not part
        of it), so a failed flow must drop the key or the user stays locked out
        for the full TTL without any email queued.
        """
        if throttle_key and self.redis_client is not None:
            with suppress(Exception):
                await self.redis_client.delete(throttle_key)

    async def send(
        self,
        *,
        uow: ApplicationUnitOfWork,
        user: User,
        throttle_key: str | None = None,
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        try:
            await self.dispatcher.enqueue_transactional(
                uow,
                self.task,
                user.email,
                user.full_name,
                throttle_key=throttle_key,
            )
        except Exception:
            # The outbox insert failed with the transaction still open: release
            # the throttle so the user can retry immediately.
            if throttle_key:
                await self.release_throttle(throttle_key)
            logger.exception(
                "Failed to enqueue %s email for %s",
                self.log_label,
                mask_email(user.email),
            )
            raise


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
