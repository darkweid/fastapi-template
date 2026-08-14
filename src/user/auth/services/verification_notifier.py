from contextlib import suppress
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import InstanceProcessingException
from src.core.outbox.dependencies import get_task_dispatcher
from src.core.outbox.dispatcher import TaskDispatcher
from src.core.redis.dependencies import get_redis_client
from src.core.utils.security import mask_email
from src.user.auth.tasks import send_verification_email_task
from src.user.models import User

logger = get_logger(__name__)


class VerificationNotifier:
    """
    Coordinates sending verification emails:
    - stores verification email delivery in the outbox of the caller's transaction,
    - performs throttling through Redis (optional).
    """

    def __init__(
        self,
        dispatcher: TaskDispatcher,
        redis_client: Redis | None = None,
        throttle_ttl_sec: int = 60,
    ) -> None:
        self.dispatcher = dispatcher
        self.redis_client = redis_client
        self.throttle_ttl_sec = throttle_ttl_sec

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(
                "We've already sent you a verification email."
            )
        await self.redis_client.setex(key, self.throttle_ttl_sec, "1")

    async def release_throttle(self, throttle_key: str | None) -> None:
        """Best-effort throttle release for flows that failed after setting it.

        The throttle key outlives a rolled-back transaction (Redis is not part
        of it), so a failed flow must drop the key or the user stays locked out
        for the full TTL without any email queued.
        """
        if throttle_key and self.redis_client is not None:
            with suppress(Exception):
                await self.redis_client.delete(throttle_key)

    async def send_verification(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        user: User,
        throttle_key: str | None = None,
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        try:
            await self.dispatcher.enqueue_transactional(
                uow,
                send_verification_email_task,
                user.email,
                user.full_name,
                throttle_key=throttle_key,
            )
        except Exception:
            # The outbox insert failed with the transaction still open: release
            # the throttle so the user can retry immediately.
            await self.release_throttle(throttle_key)
            logger.exception(
                "Failed to enqueue verification email for %s",
                mask_email(user.email),
            )
            raise


def get_verification_notifier(
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
) -> VerificationNotifier:
    return VerificationNotifier(dispatcher=dispatcher, redis_client=redis_client)
