import contextlib
import os
from pathlib import Path
from typing import Any, cast

from fastapi_mail import MessageType
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError

from celery_tasks.types import CeleryTask
from loggers import get_logger
from src.core.email_service.interfaces import AbstractMailer
from src.core.email_service.tasks import (
    send_email_task,
    send_email_with_file_task,
)

logger = get_logger(__name__)


class EmailService:
    _email_adapter = TypeAdapter(EmailStr)

    def __init__(self, mailer: AbstractMailer):
        self._mailer = mailer

    async def send_template_email(
        self,
        subject: str,
        recipients: str | list[str],
        template_name: str,
        template_body: BaseModel | dict[str, Any],
        subtype: MessageType = MessageType.html,
    ) -> None:
        normalized = self._normalize_and_validate_recipients(recipients)
        try:
            await self._mailer.send_template(
                subject,
                [str(e) for e in normalized],
                template_name,
                template_body,
                subtype.value,
            )
            logger.debug("Email '%s' sent to %s", template_name, normalized)
        except Exception as e:
            logger.error("Failed to send template email: %s", e)
            raise

    async def send_template_email_with_delay(
        self,
        subject: str,
        recipients: str | list[str],
        template_name: str,
        template_body: BaseModel | dict[str, Any],
        subtype: MessageType = MessageType.html,
    ) -> None:
        normalized = self._normalize_and_validate_recipients(recipients)
        try:
            # Handle both BaseModel and dict types
            template_data = (
                template_body
                if isinstance(template_body, dict)
                else template_body.model_dump()
            )
            task = cast(CeleryTask, send_email_task)
            task.delay(
                subject,
                [str(e) for e in normalized],
                template_name,
                template_data,
                subtype.value,
            )
            logger.debug("Email task queued for %s", normalized)
        except Exception as e:
            logger.error("Failed to queue template email task: %s", e)
            raise

    async def send_file_to_email_with_delay(
        self,
        subject: str,
        recipients: str | list[str],
        attachments: list[Path],
        subtype: MessageType = MessageType.plain,
    ) -> None:
        validated_recipients = self._normalize_and_validate_recipients(recipients)

        task = cast(CeleryTask, send_email_with_file_task)
        task.delay(
            subject,
            [str(e) for e in validated_recipients],
            [str(path) for path in attachments],
            subtype.value,
        )

        logger.debug("Attachment email task queued for %s", validated_recipients)

    async def send_email_with_attachments(
        self,
        subject: str,
        recipients: str | list[str],
        body_text: str,
        file_paths: list[Path],
        subtype: MessageType = MessageType.plain,
    ) -> None:
        try:
            validated_recipients = self._normalize_and_validate_recipients(recipients)

            await self._mailer.send_with_attachments(
                subject,
                [str(e) for e in validated_recipients],
                body_text,
                file_paths,
                subtype.value,
            )
            logger.debug("Email with attachments sent to %s", validated_recipients)

        except Exception as e:
            logger.error("Failed to send email with attachments: %s", e)
            raise

        finally:
            for file_path in file_paths:
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    os.unlink(file_path)

    async def send_email_with_single_attachment(
        self,
        subject: str,
        recipients: str | list[str],
        body_text: str,
        file_path: Path,
        subtype: MessageType = MessageType.plain,
    ) -> None:
        """
        Send an email with a single attachment.

        Args:
            subject (str): Email subject.
            recipients (Union[str, List[str]]): One or more recipient email addresses.
            body_text (str): Email body.
            file_path (Path): Path to the single file attachment.
            subtype (MessageType): Email content type. Defaults to plain.
        """
        await self.send_email_with_attachments(
            subject=subject,
            recipients=recipients,
            body_text=body_text,
            file_paths=[file_path],
            subtype=subtype,
        )

    def _normalize_and_validate_recipients(
        self, recipients: str | list[str]
    ) -> list[EmailStr]:
        """
        Normalize and validate email recipients.

        Converts input (string or list) into a validated list of EmailStr.
        Invalid addresses are skipped with a warning. Raises if none are valid.
        """
        if isinstance(recipients, str):
            recipients = [recipients]

        validated = []
        for email in recipients:
            try:
                validated.append(self._email_adapter.validate_python(email))
            except ValidationError:
                logger.warning("Invalid email address skipped: %s", email)

        if not validated:
            raise ValueError("No valid recipient emails provided.")

        return validated
