from pathlib import Path
from typing import Any

from fastapi_mail import MessageType

from loggers import get_logger
from src.core.email_service.config import get_fastapi_mail_config
from src.core.email_service.fastapi_mailer import FastAPIMailer
from src.core.email_service.interfaces import AbstractMailer
from src.core.utils.security import mask_email
from taskiq_worker.broker import broker

logger = get_logger(__name__)


def get_mailer() -> AbstractMailer:
    """Build a mailer from config; tasks construct it per run."""
    config = get_fastapi_mail_config()
    return FastAPIMailer(config)


@broker.task(task_name="send_email", retry_on_error=True)
async def send_email_task(
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
    subtype: str = "html",
) -> None:
    mailer = get_mailer()
    try:
        subtype_enum = MessageType(subtype)
        await mailer.send_template(
            subject=subject,
            recipients=recipients,
            template_data=context,
            template_name=template_name,
            subtype=subtype_enum.value,
        )
        logger.info(
            "Email successfully sent to %s", [mask_email(r) for r in recipients]
        )
    except Exception as e:
        logger.exception("Failed to send email: %s", e)
        raise


@broker.task(task_name="send_email_with_file", retry_on_error=True)
async def send_email_with_file_task(
    subject: str,
    recipients: list[str],
    attachments: list[str],
    subtype: str = "plain",
) -> None:
    mailer = get_mailer()
    try:
        subtype_enum = MessageType(subtype)
        await mailer.send_with_attachments(
            subject=subject,
            recipients=recipients,
            body_text="",
            file_paths=[Path(path) for path in attachments],
            subtype=subtype_enum.value,
        )
        logger.info(
            "Email with attachment sent to %s", [mask_email(r) for r in recipients]
        )
    except Exception as e:
        logger.exception("Failed to send email with attachment: %s", e)
        raise
