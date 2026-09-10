from contextlib import suppress
from typing import Annotated

from redis.asyncio import Redis
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.auth.one_time_tokens import issue_one_time_token
from src.core.email_service.schemas import (
    MailTemplateResetPasswordBody,
    MailTemplateVerificationBody,
)
from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer
from src.core.utils.security import mask_email
from src.core.utils.urls import build_public_url
from src.main.config import config
from src.user.auth.realm import (
    RESET_PASSWORD_PURPOSE,
    USER_AUTH_REALM,
    VERIFICATION_PURPOSE,
)
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client

logger = get_logger(__name__)


async def _deliver_tokenized_email(
    *,
    redis_client: Redis,
    email: str,
    purpose: str,
    mode: str,
    ttl_minutes: int,
    link_path: str,
    subject: str,
    template_name: str,
    template_body: MailTemplateVerificationBody | MailTemplateResetPasswordBody,
    throttle_key: str | None,
) -> None:
    """Issue a one-time token, send its link by email, clean up on failure.

    Token creation happens inside the cleanup scope on purpose: if it fails
    (for example a transient Redis error), the throttle key must be released
    just as when the send itself fails, or the user stays locked out of
    resends until the throttle TTL expires.

    The `link` field on `template_body` is a placeholder: it is only known
    once the token is turned into a public URL here, so callers pass the rest
    of the template already filled in and this replaces just that field.
    """
    email_service = EmailService(get_mailer())
    try:
        token = await issue_one_time_token(
            realm=USER_AUTH_REALM,
            purpose=purpose,
            identifier=email,
            mode=mode,
            ttl_minutes=ttl_minutes,
            redis_client=redis_client,
        )
        link = build_public_url(config.app.PUBLIC_BASE_URL, link_path, token=token)
        await email_service.send_template_email(
            subject=subject,
            recipients=email,
            template_name=template_name,
            template_body=template_body.model_copy(update={"link": link}),
        )
    except Exception:
        # Retire the challenge before releasing the throttle, never after: the
        # throttle is what keeps a resend or a retry from issuing a new
        # challenge in between, and invalidate() deletes whatever is live by
        # then - which would be that new one, leaving the user holding a link
        # that no longer decodes.
        with suppress(Exception):
            await ActiveChallengeRegistry(USER_AUTH_REALM).invalidate(
                purpose, email, redis_client
            )
        if throttle_key:
            with suppress(Exception):
                await redis_client.delete(throttle_key)
        logger.exception(
            "Failed to process %s email task for %s", purpose, mask_email(email)
        )
        raise


@broker.task(task_name="send_verification_email", retry_on_error=True)
async def send_verification_email_task(
    email: str,
    full_name: str,
    *,
    throttle_key: str | None = None,
    redis_client: Annotated[Redis, TaskiqDepends(get_tasks_redis_client)],
) -> None:
    await _deliver_tokenized_email(
        redis_client=redis_client,
        email=email,
        purpose=VERIFICATION_PURPOSE,
        mode="verification_token",
        ttl_minutes=config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES,
        link_path=config.app.EMAIL_VERIFY_PATH,
        subject="Verification Message",
        template_name="verification.html",
        template_body=MailTemplateVerificationBody(
            title="Verification Message", link="", name=full_name
        ),
        throttle_key=throttle_key,
    )


@broker.task(task_name="send_reset_password_email", retry_on_error=True)
async def send_reset_password_email_task(
    email: str,
    full_name: str,
    *,
    throttle_key: str | None = None,
    redis_client: Annotated[Redis, TaskiqDepends(get_tasks_redis_client)],
) -> None:
    await _deliver_tokenized_email(
        redis_client=redis_client,
        email=email,
        purpose=RESET_PASSWORD_PURPOSE,
        mode="reset_password_token",
        ttl_minutes=config.jwt.RESET_PASSWORD_TOKEN_EXPIRE_MINUTES,
        link_path=config.app.PASSWORD_RESET_PATH,
        subject="Resetting password",
        template_name="reset_password.html",
        template_body=MailTemplateResetPasswordBody(
            title="Restore access", link="", name=full_name
        ),
        throttle_key=throttle_key,
    )
