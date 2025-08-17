from pathlib import Path

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from pydantic import EmailStr, BaseModel

from src.core.email.interfaces import AbstractMailer


class FastAPIMailer(AbstractMailer):
    def __init__(self, config: ConnectionConfig):
        self._mailer = FastMail(config)

    async def send_template(
        self,
        subject: str,
        recipients: list[EmailStr],
        template_name: str,
        template_data: BaseModel,
        subtype: str = "html",
    ) -> None:
        """
        Send an email based on a Jinja2 template.

        Args:
            subject (str): The subject of the email.
            recipients (List[EmailStr]): List of recipient email addresses.
            template_name (str): Name of the Jinja2 template file.
            template_data (dict): Context data to render inside the template.
            subtype (MessageType, optional): Email content type (html or plain). Defaults to html.
        """
        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            template_body=template_data.model_dump(),
            subtype=subtype,
        )
        await self._mailer.send_message(message, template_name=template_name)

    async def send_with_attachments(
        self,
        subject: str,
        recipients: list[EmailStr],
        body_text: str,
        file_paths: list[Path],
        subtype: str = "plain",
    ) -> None:
        message = MessageSchema(
            subject=subject,
            recipients=recipients,
            body=body_text,
            attachments=[str(path) for path in file_paths],
            subtype=subtype,
        )
        await self._mailer.send_message(message)
