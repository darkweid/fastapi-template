from redis.asyncio import Redis
from starlette.datastructures import URL

from src.core.email_service.schemas import MailTemplateResetPasswordBody
from src.core.email_service.service import EmailService
from src.core.errors.exceptions import InstanceProcessingException
from src.user.auth.security import create_reset_password_token
from src.user.models import User


class ResetPasswordNotifier:
    """
    Coordinates sending password-reset emails:
    - generates a token,
    - builds a link,
    - sends an email,
    - performs throttling through Redis (optional).
    """

    def __init__(
        self,
        email_service: EmailService,
        redis_client: Redis | None = None,
        throttle_ttl_sec: int = 60,
        reset_password_path: str = "v1/users/auth/password/reset/confirm",  # ToDo: adjust link with frontend here
    ) -> None:
        self.email_service = email_service
        self.redis_client = redis_client
        self.throttle_ttl_sec = throttle_ttl_sec
        self.reset_password_path = reset_password_path

    def _build_link(self, base_url: URL, token: str) -> str:
        return f"{base_url}{self.reset_password_path}?token={token}"

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(
                "We've already send you a reset-password email."
            )
        await self.redis_client.setex(key, time=self.throttle_ttl_sec, value="1")

    async def send_password_reset_email(
        self, user: User, base_url: URL, throttle_key: str | None = None
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        token = create_reset_password_token({"email": user.email})
        link = self._build_link(base_url, token)
        await self.email_service.send_template_email_with_delay(
            subject="Resetting password",
            recipients=user.email,
            template_name="reset_password.html",
            template_body=MailTemplateResetPasswordBody(
                title="Restore access",
                link=link,
                name=user.full_name,
            ),
        )
