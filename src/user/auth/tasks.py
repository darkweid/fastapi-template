from contextlib import suppress
from typing import Annotated

from redis.asyncio import Redis
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.auth.challenges import ActiveChallengeRegistry, delete_if_value
from src.core.auth.errors import ChallengeAlreadyLiveError
from src.core.auth.one_time_tokens import issue_one_time_token
from src.core.email_service.schemas import (
    MailTemplateResetPasswordBody,
    MailTemplateVerificationBody,
)
from src.core.email_service.service import EmailService
from src.core.email_service.tasks import get_mailer
from src.core.errors.describe import scrub
from src.core.utils.security import mask_email
from src.core.utils.urls import build_public_url
from src.main.config import config
from src.user.auth.realm import (
    RESET_PASSWORD_PURPOSE,
    USER_AUTH_REALM,
    VERIFICATION_PURPOSE,
)
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client, is_retry

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
    throttle_owner: str | None,
    retry: bool,
) -> None:
    """Issue a one-time token, send its link by email, clean up on failure.

    A failed run takes back only what it holds itself: the throttle slot is
    free again by the time the retry runs, so the address may have a newer send
    with a slot and a challenge of its own. A retry that finds a challenge live
    sends nothing for the same reason - the newer link reaches the same mailbox.
    One exception: if the failed run could not retire its own challenge (Redis
    unreachable), the retry stops at that one and the user asks for a new link.
    The retry cannot tell the two apart, hence the warning.

    Token creation happens inside the cleanup scope on purpose: if it fails
    (for example a transient Redis error), the throttle key must be released
    just as when the send itself fails, or the user stays locked out of
    resends until the throttle TTL expires.

    The `link` field on `template_body` is a placeholder: it is only known
    once the token is turned into a public URL here, so callers pass the rest
    of the template already filled in and this replaces just that field.
    """
    email_service = EmailService(get_mailer())
    jti: str | None = None
    try:
        token, jti = await issue_one_time_token(
            realm=USER_AUTH_REALM,
            purpose=purpose,
            identifier=email,
            mode=mode,
            ttl_minutes=ttl_minutes,
            redis_client=redis_client,
            only_if_absent=retry,
        )
        link = build_public_url(config.app.PUBLIC_BASE_URL, link_path, token=token)
        await email_service.send_template_email(
            subject=subject,
            recipients=email,
            template_name=template_name,
            template_body=template_body.model_copy(update={"link": link}),
        )
    except ChallengeAlreadyLiveError:
        logger.warning(
            "Skipped a retried %s email for %s: a challenge is live",
            purpose,
            mask_email(email),
        )
    except Exception as error:
        if jti:
            with suppress(Exception):
                await ActiveChallengeRegistry(USER_AUTH_REALM).retire(
                    purpose, email, jti, redis_client
                )
        if throttle_key and throttle_owner:
            with suppress(Exception):
                await delete_if_value(throttle_key, throttle_owner, redis_client)
        # An SMTP error quotes the address and a Redis one its command, and taskiq
        # logs whatever leaves the task, so a stand-in is raised, unchained.
        failure = scrub(error)
        logger.error(
            "Failed to process %s email task for %s",
            purpose,
            mask_email(email),
            exc_info=failure,
        )
        raise failure from None


@broker.task(task_name="send_verification_email", retry_on_error=True)
async def send_verification_email_task(
    email: str,
    full_name: str,
    *,
    throttle_key: str | None = None,
    throttle_owner: str | None = None,
    retry: Annotated[bool, TaskiqDepends(is_retry)],
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
        throttle_owner=throttle_owner,
        retry=retry,
    )


@broker.task(task_name="send_reset_password_email", retry_on_error=True)
async def send_reset_password_email_task(
    email: str,
    full_name: str,
    *,
    throttle_key: str | None = None,
    throttle_owner: str | None = None,
    retry: Annotated[bool, TaskiqDepends(is_retry)],
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
        throttle_owner=throttle_owner,
        retry=retry,
    )
