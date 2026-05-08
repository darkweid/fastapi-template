from contextlib import suppress

from celery_tasks.types import typed_shared_task
from loggers import get_logger
from src.core.email_service.schemas import (
    MailTemplateResetPasswordBody,
    MailTemplateVerificationBody,
)
from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer
from src.core.redis.core import create_redis_client
from src.core.utils.coroutine_runner import execute_coroutine_sync
from src.main.config import config
from src.user.auth.security import (
    create_reset_password_token,
    create_verification_token,
)
from src.user.auth.token_helpers import invalidate_active_one_time_token

logger = get_logger(__name__)


@typed_shared_task(name="send_verification_email")
def send_verification_email_task(
    email: str,
    full_name: str,
    base_url: str,
    verify_path: str = "v1/users/auth/verify",
    throttle_key: str | None = None,
) -> None:
    execute_coroutine_sync(
        coroutine=_send_verification_email(
            email=email,
            full_name=full_name,
            base_url=base_url,
            verify_path=verify_path,
            throttle_key=throttle_key,
        )
    )


async def _send_verification_email(
    *,
    email: str,
    full_name: str,
    base_url: str,
    verify_path: str,
    throttle_key: str | None,
) -> None:
    redis_client = create_redis_client(
        config.redis.dsn
    )  # TODO: consider connection pooling for non-solo worker pools
    email_service = EmailService(get_mailer())

    try:
        token = await create_verification_token(
            {"email": email},
            redis_client=redis_client,
        )
        link = f"{base_url}{verify_path}?token={token}"
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
        logger.exception("Failed to process verification email task for %s", email)
        raise
    finally:
        await redis_client.aclose()


@typed_shared_task(name="send_reset_password_email")
def send_reset_password_email_task(
    email: str,
    full_name: str,
    base_url: str,
    reset_link_path: str = "v1/users/auth/password/reset/confirm",
    throttle_key: str | None = None,
) -> None:
    execute_coroutine_sync(
        coroutine=_send_reset_password_email(
            email=email,
            full_name=full_name,
            base_url=base_url,
            reset_link_path=reset_link_path,
            throttle_key=throttle_key,
        )
    )


async def _send_reset_password_email(
    *,
    email: str,
    full_name: str,
    base_url: str,
    reset_link_path: str,
    throttle_key: str | None,
) -> None:
    redis_client = create_redis_client(
        config.redis.dsn
    )  # TODO: consider connection pooling for non-solo worker pools
    email_service = EmailService(get_mailer())

    try:
        token = await create_reset_password_token(
            {"email": email},
            redis_client=redis_client,
        )
        link = f"{base_url}{reset_link_path}?token={token}"
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
        logger.exception("Failed to process password reset email task for %s", email)
        raise
    finally:
        await redis_client.aclose()
