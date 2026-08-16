from contextlib import suppress
from typing import Annotated

from redis.asyncio import Redis
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.email_service.schemas import (
    MailTemplateResetPasswordBody,
    MailTemplateVerificationBody,
)
from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer
from src.core.utils.security import mask_email
from src.core.utils.urls import build_public_url
from src.main.config import config
from src.user.auth.redis_keys import OneTimeTokenPurpose
from src.user.auth.security import (
    create_reset_password_token,
    create_verification_token,
)
from src.user.auth.token_helpers import invalidate_active_one_time_token
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client

logger = get_logger(__name__)


async def _deliver_tokenized_email(
    *,
    redis_client: Redis,
    email: str,
    token: str,
    link_path: str,
    subject: str,
    template_name: str,
    template_body: MailTemplateVerificationBody | MailTemplateResetPasswordBody,
    purpose: OneTimeTokenPurpose,
    throttle_key: str | None,
) -> None:
    """Resolve a one-time token into a link, send the email, clean up on failure.

    The `link` field on `template_body` is a placeholder: it is only known
    once the token is turned into a public URL here, so callers pass the rest
    of the template already filled in and this replaces just that field.
    """
    email_service = EmailService(get_mailer())
    try:
        link = build_public_url(config.app.PUBLIC_BASE_URL, link_path, token=token)
        await email_service.send_template_email(
            subject=subject,
            recipients=email,
            template_name=template_name,
            template_body=template_body.model_copy(update={"link": link}),
        )
    except Exception:
        if throttle_key:
            with suppress(Exception):
                await redis_client.delete(throttle_key)
        with suppress(Exception):
            await invalidate_active_one_time_token(
                purpose=purpose,
                email=email,
                redis_client=redis_client,
            )
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
    token = await create_verification_token({"email": email}, redis_client=redis_client)
    await _deliver_tokenized_email(
        redis_client=redis_client,
        email=email,
        token=token,
        link_path=config.app.EMAIL_VERIFY_PATH,
        subject="Verification Message",
        template_name="verification.html",
        template_body=MailTemplateVerificationBody(
            title="Verification Message", link="", name=full_name
        ),
        purpose="verification",
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
    token = await create_reset_password_token(
        {"email": email}, redis_client=redis_client
    )
    await _deliver_tokenized_email(
        redis_client=redis_client,
        email=email,
        token=token,
        link_path=config.app.PASSWORD_RESET_PATH,
        subject="Resetting password",
        template_name="reset_password.html",
        template_body=MailTemplateResetPasswordBody(
            title="Restore access", link="", name=full_name
        ),
        purpose="reset_password",
        throttle_key=throttle_key,
    )
