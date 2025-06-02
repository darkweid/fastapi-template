import logging
import os
from pathlib import Path
from typing import Union, List

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

from app.core.schemas import Base

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, config: ConnectionConfig):
        self._mailer = FastMail(config)

    async def send_template_email(
            self,
            subject: str,
            recipients: Union[str, List[str]],
            template_name: str,
            template_body: Base,
            subtype: str = "html"
    ) -> None:
        if isinstance(recipients, str):
            recipients = [recipients]

        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            template_body=template_body.model_dump(),
            subtype=subtype,
        )

        await self._mailer.send_message(message, template_name=template_name)
        logger.info("Email '%s' sent to %s", template_name, recipients)

    async def send_email_with_attachment(
            self,
            subject: str,
            recipients: Union[str, List[str]],
            body_text: str,
            file_path: Path,
            subtype: str = "plain"
    ) -> None:
        if isinstance(recipients, str):
            recipients = [recipients]

        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            body=body_text,
            attachments=[str(file_path)],
            subtype=subtype,
        )

        await self._mailer.send_message(message)
        logger.info("Email with attachment sent to %s", recipients)

        try:
            os.unlink(file_path)
        except Exception as e:
            logger.warning("Could not delete file: %s", e)
