from redis.asyncio import Redis
from starlette.datastructures import URL

from src.core.email_service.schemas import MailTemplateVerificationBody
from src.core.email_service.service import EmailService
from src.core.errors.exceptions import InstanceProcessingException
from src.user.auth.security import create_verification_token
from src.user.models import User


class VerificationNotifier:
    """
    Coordinates sending verification emails:
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
        verify_path: str = "v1/users/auth/verify",
    ) -> None:
        self.email_service = email_service
        self.redis_client = redis_client
        self.throttle_ttl_sec = throttle_ttl_sec
        self.verify_path = verify_path

    def _build_link(self, base_url: URL, token: str) -> str:
        return f"{base_url}{self.verify_path}?token={token}"

    async def _throttle_or_touch(self, key: str | None) -> None:
        if not key or not self.redis_client:
            return
        existing = await self.redis_client.get(key)
        if existing:
            raise InstanceProcessingException(
                "We've already send you a verification email."
            )
        await self.redis_client.setex(key, time=self.throttle_ttl_sec, value="1")

    async def send_verification(
        self, user: User, base_url: URL, throttle_key: str | None = None
    ) -> None:
        await self._throttle_or_touch(throttle_key)
        token = create_verification_token({"email": user.email})
        link = self._build_link(base_url, token)
        await self.email_service.send_template_email_with_delay(
            subject="Verification Message",
            recipients=user.email,
            template_name="verification.html",
            template_body=MailTemplateVerificationBody(
                title="Verification Message",
                link=link,
                name=user.full_name,
            ),
        )
