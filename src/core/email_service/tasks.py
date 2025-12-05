from pathlib import Path
from typing import Any

from asgiref.sync import async_to_sync
from fastapi_mail import MessageType

from celery_tasks.main import celery_app  # noqa: F401
from celery_tasks.types import typed_shared_task
from loggers import get_logger
from src.core.email_service.config import get_fastapi_mail_config
from src.core.email_service.fastapi_mailer import FastAPIMailer
from src.core.email_service.interfaces import AbstractMailer

logger = get_logger(__name__)


def get_mailer() -> AbstractMailer:
    """
    Retrieve the mailer instance from the Celery app context.
    This function is used to ensure that the mailer is available
    in the Celery task context.
    """

    config = get_fastapi_mail_config()
    return FastAPIMailer(config)


@typed_shared_task
def send_email_task(
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
    subtype: str = "html",
) -> None:
    """
    Celery task to send email asynchronously via FastAPI-Mail.
    """
    mailer = get_mailer()
    try:
        subtype_enum = MessageType(subtype)
        async_to_sync(mailer.send_template)(
            subject=subject,
            recipients=recipients,
            template_data=context,
            template_name=template_name,
            subtype=subtype_enum.value,
        )
        logger.info("Email successfully sent via Celery to %s", recipients)

    except Exception as e:
        logger.exception("Failed to send email via Celery: %s", e)
        raise


@typed_shared_task
def send_email_with_file_task(
    subject: str,
    recipients: list[str],
    attachments: list[str],
    subtype: str = "plain",
) -> None:
    """
    Send email with multiple attachments via Celery.
    """
    mailer = get_mailer()
    try:
        subtype_enum = MessageType(subtype)
        async_to_sync(mailer.send_with_attachments)(
            subject=subject,
            recipients=recipients,
            body_text="",
            file_paths=[Path(path) for path in attachments],
            subtype=subtype_enum.value,
        )
        logger.info("Email with attachment sent to %s", recipients)

    except Exception as e:
        logger.exception("Failed to send email with attachment: %s", e)
        raise
