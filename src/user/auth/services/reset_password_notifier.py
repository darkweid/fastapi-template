from typing import cast

from fastapi import Depends
from redis.asyncio import Redis
from starlette.datastructures import URL

from celery_tasks.types import CeleryTask
from loggers import get_logger
from src.core.errors.exceptions import InstanceProcessingException
from src.core.redis.dependencies import get_redis_client
from src.user.auth.tasks import send_reset_password_email_task
from src.user.models import User

logger = get_logger(__name__)


class ResetPasswordNotifier:
    """
    Coordinates sending password-reset emails:
    - enqueues password-reset email delivery,
    - performs throttling through Redis (optional).
    """

    def __init__(
        self,
        redis_client: Redis | None = None,
        throttle_ttl_sec: int = 60,
        reset_link_path: str = "v1/users/auth/password/reset/confirm",  # ToDo: adjust link with frontend here
    ) -> None:
        self.redis_client = redis_client
        self.throttle_ttl_sec = throttle_ttl_sec
        self.reset_link_path = reset_link_path

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(
                "We've already send you a reset-password email."
            )
        await self.redis_client.setex(key, self.throttle_ttl_sec, "1")

    async def send_password_reset_email(
        self, user: User, base_url: URL, throttle_key: str | None = None
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        try:
            task = cast(CeleryTask, send_reset_password_email_task)
            task.delay(
                user.email,
                user.full_name,
                str(base_url),
                self.reset_link_path,
                throttle_key,
            )
        except Exception:
            if throttle_key and self.redis_client is not None:
                await self.redis_client.delete(throttle_key)
            logger.exception(
                "Failed to queue password reset email for %s",
                user.email,
            )
            raise


def get_reset_password_notifier(
    redis_client: Redis = Depends(get_redis_client),
) -> ResetPasswordNotifier:
    return ResetPasswordNotifier(redis_client=redis_client)
