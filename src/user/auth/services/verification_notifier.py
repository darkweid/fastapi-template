from typing import cast

from fastapi import Depends
from redis.asyncio import Redis
from starlette.datastructures import URL

from celery_tasks.types import CeleryTask
from loggers import get_logger
from src.core.errors.exceptions import InstanceProcessingException
from src.core.redis.dependencies import get_redis_client
from src.user.auth.tasks import send_verification_email_task
from src.user.models import User

logger = get_logger(__name__)


class VerificationNotifier:
    """
    Coordinates sending verification emails:
    - enqueues verification email delivery,
    - performs throttling through Redis (optional).
    """

    def __init__(
        self,
        redis_client: Redis | None = None,
        throttle_ttl_sec: int = 60,
        verify_path: str = "v1/users/auth/verify",
    ) -> None:
        self.redis_client = redis_client
        self.throttle_ttl_sec = throttle_ttl_sec
        self.verify_path = verify_path

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(
                "We've already send you a verification email."
            )
        await self.redis_client.setex(key, self.throttle_ttl_sec, "1")

    async def send_verification(
        self, user: User, base_url: URL, throttle_key: str | None = None
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        try:
            task = cast(CeleryTask, send_verification_email_task)
            task.delay(
                user.email,
                user.full_name,
                str(base_url),
                self.verify_path,
                throttle_key,
            )
        except Exception:
            if throttle_key and self.redis_client is not None:
                await self.redis_client.delete(throttle_key)
            logger.exception(
                "Failed to queue verification email for %s",
                user.email,
            )
            raise


def get_verification_notifier(
    redis_client: Redis = Depends(get_redis_client),
) -> VerificationNotifier:
    return VerificationNotifier(redis_client=redis_client)
