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
from src.user.auth.security import (
    create_reset_password_token,
    create_verification_token,
)
from src.user.auth.token_helpers import invalidate_active_one_time_token
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client

logger = get_logger(__name__)


@broker.task(task_name="send_verification_email", retry_on_error=True)
async def send_verification_email_task(
    email: str,
    full_name: str,
    throttle_key: str | None = None,
    *,
    redis_client: Annotated[Redis, TaskiqDepends(get_tasks_redis_client)],
) -> None:
    email_service = EmailService(get_mailer())
    try:
        token = await create_verification_token(
            {"email": email},
            redis_client=redis_client,
        )
        link = build_public_url(
            config.app.PUBLIC_BASE_URL,
            config.app.EMAIL_VERIFY_PATH,
            token=token,
        )
        await email_service.send_template_email(
            subject="Verification Message",
            recipients=email,
            template_name="verification.html",
            template_body=MailTemplateVerificationBody(
                title="Verification Message",
                link=link,
                name=full_name,
            ),
        )
    except Exception:
        if throttle_key:
            with suppress(Exception):
                await redis_client.delete(throttle_key)
        with suppress(Exception):
            await invalidate_active_one_time_token(
                purpose="verification",
                email=email,
                redis_client=redis_client,
            )
        logger.exception(
            "Failed to process verification email task for %s", mask_email(email)
        )
        raise


@broker.task(task_name="send_reset_password_email", retry_on_error=True)
async def send_reset_password_email_task(
    email: str,
    full_name: str,
    throttle_key: str | None = None,
    *,
    redis_client: Annotated[Redis, TaskiqDepends(get_tasks_redis_client)],
) -> None:
    email_service = EmailService(get_mailer())
    try:
        token = await create_reset_password_token(
            {"email": email},
            redis_client=redis_client,
        )
        link = build_public_url(
            config.app.PUBLIC_BASE_URL,
            config.app.PASSWORD_RESET_PATH,
            token=token,
        )
        await email_service.send_template_email(
            subject="Resetting password",
            recipients=email,
            template_name="reset_password.html",
            template_body=MailTemplateResetPasswordBody(
                title="Restore access",
                link=link,
                name=full_name,
            ),
        )
    except Exception:
        if throttle_key:
            with suppress(Exception):
                await redis_client.delete(throttle_key)
        with suppress(Exception):
            await invalidate_active_one_time_token(
                purpose="reset_password",
                email=email,
                redis_client=redis_client,
            )
        logger.exception(
            "Failed to process password reset email task for %s", mask_email(email)
        )
        raise
