from pathlib import Path
from typing import Any

from loggers import get_logger
from src.core.email_service.enums import MessageType
from src.core.email_service.interfaces import AbstractMailer
from src.core.email_service.smtp_mailer import SmtpMailer
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


@broker.task(task_name="send_email_with_file", retry_on_error=True)
async def send_email_with_file_task(
    subject: str,
    recipients: list[str],
    attachments: list[str],
    subtype: str = MessageType.PLAIN,
) -> None:
    mailer = get_mailer()
    try:
        await mailer.send_with_attachments(
            subject=subject,
            recipients=recipients,
            body_text="",
            file_paths=[Path(path) for path in attachments],
            subtype=MessageType(subtype),
        )
        logger.info(
            "Email with attachment sent to %s", [mask_email(r) for r in recipients]
        )
    except Exception as e:
        logger.exception("Failed to send email with attachment: %s", e)
        raise
