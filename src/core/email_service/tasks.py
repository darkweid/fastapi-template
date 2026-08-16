from pathlib import PurePosixPath
from typing import Any

from loggers import get_logger
from src.core.email_service.enums import MessageType
from src.core.email_service.interfaces import AbstractMailer
from src.core.email_service.smtp_mailer import SmtpMailer
from src.core.errors.exceptions import InfrastructureException
from src.core.storage.s3.dependencies import build_s3_adapter
from src.core.utils.security import mask_email
from src.main.config import config
from taskiq_worker.broker import broker

logger = get_logger(__name__)


def get_mailer() -> AbstractMailer:
    """Build a mailer from config; tasks construct it per run."""
    return SmtpMailer(config.broadcasting)


@broker.task(task_name="send_email", retry_on_error=True)
async def send_email_task(
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
    subtype: str = MessageType.HTML,
) -> None:
    mailer = get_mailer()
    try:
        await mailer.send_template(
            subject=subject,
            recipients=recipients,
            template_data=context,
            template_name=template_name,
            subtype=MessageType(subtype),
        )
        logger.info(
            "Email successfully sent to %s", [mask_email(r) for r in recipients]
        )
    except Exception as e:
        logger.exception("Failed to send email: %s", e)
        raise


@broker.task(task_name="send_email_with_s3_attachments", retry_on_error=True)
async def send_email_with_s3_attachments_task(
    subject: str,
    recipients: list[str],
    attachment_keys: list[str],
    subtype: str = MessageType.PLAIN,
    *,
    cleanup: bool = False,
) -> None:
    # Defensive: the outbox row can outlive a config flip, so a task picked up
    # after S3 was disabled must fail loudly rather than crash on a missing adapter.
    if not config.s3.S3_ENABLED:
        raise InfrastructureException(
            "S3 is disabled: set S3_ENABLED=true and provide S3 credentials"
        )

    mailer = get_mailer()
    try:
        async with build_s3_adapter(config.s3) as s3:
            attachments = [
                (PurePosixPath(key).name, await s3.download_bytes(key))
                for key in attachment_keys
            ]
            await mailer.send_with_attachment_bytes(
                subject=subject,
                recipients=recipients,
                body_text="",
                attachments=attachments,
                subtype=MessageType(subtype),
            )
            # Deleting only after a confirmed send keeps a failed attempt (or a
            # retry) able to re-download the same keys and try again.
            if cleanup:
                failed_keys: list[str] = []
                for key in attachment_keys:
                    # Delivery already succeeded above; a delete failure must never
                    # propagate here, or SmartRetryMiddleware would rerun the whole
                    # task and resend the email that already went out.
                    try:
                        await s3.delete_object(key)
                    except Exception:
                        failed_keys.append(key)
                if failed_keys:
                    logger.warning(
                        "Failed to delete S3 attachment(s) after send: %s",
                        failed_keys,
                    )
        logger.info(
            "Email with S3 attachments sent to %s", [mask_email(r) for r in recipients]
        )
    except Exception as e:
        logger.exception("Failed to send email with S3 attachments: %s", e)
        raise
