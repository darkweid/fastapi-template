import asyncio
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
import mimetypes
from pathlib import Path
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from src.core.email_service.enums import MessageType
from src.core.email_service.interfaces import AbstractMailer
from src.core.utils.datetime_utils import get_utc_now
from src.main.config import BroadcastingConfig

TEMPLATES_DIRECTORY = Path(__file__).parent / "templates"

DEFAULT_ATTACHMENT_MIME_TYPE = "application/octet-stream"


def build_template_environment(directory: Path = TEMPLATES_DIRECTORY) -> Environment:
    """Build the Jinja2 environment used to render email bodies."""
    return Environment(
        loader=FileSystemLoader(directory),
        autoescape=select_autoescape(["html", "htm", "xml"]),
    )


class SmtpMailer(AbstractMailer):
    """Renders Jinja2 templates and delivers them over SMTP via aiosmtplib."""

    def __init__(
        self,
        config: BroadcastingConfig,
        template_environment: Environment | None = None,
    ) -> None:
        self._config = config
        self._template_environment = (
            template_environment or build_template_environment()
        )

    async def send_template(
        self,
        subject: str,
        recipients: list[str],
        template_name: str,
        template_data: BaseModel | dict[str, Any],
        subtype: MessageType = MessageType.HTML,
    ) -> None:
        context = (
            template_data
            if isinstance(template_data, dict)
            else template_data.model_dump()
        )
        template = self._template_environment.get_template(template_name)
        message = self._build_message(subject, recipients)
        message.set_content(template.render(**context), subtype=subtype.value)

        await self._send(message)

    async def send_with_attachments(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        file_paths: list[Path],
        subtype: MessageType = MessageType.PLAIN,
    ) -> None:
        message = self._build_message(subject, recipients)
        message.set_content(body_text, subtype=subtype.value)
        for file_path in file_paths:
            await self._attach_file(message, file_path)

        await self._send(message)

    async def send_with_attachment_bytes(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        attachments: list[tuple[str, bytes]],
        subtype: MessageType = MessageType.PLAIN,
    ) -> None:
        message = self._build_message(subject, recipients)
        message.set_content(body_text, subtype=subtype.value)
        for filename, data in attachments:
            self._attach_bytes(message, filename, data)

        await self._send(message)

    def _build_message(self, subject: str, recipients: list[str]) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr(
            (self._config.EMAIL_FROM_NAME, self._config.EMAIL_USER)
        )
        message["To"] = ", ".join(recipients)
        message["Date"] = format_datetime(get_utc_now())
        message["Message-ID"] = make_msgid()
        return message

    async def _attach_file(self, message: EmailMessage, file_path: Path) -> None:
        payload = await asyncio.to_thread(file_path.read_bytes)
        self._attach_bytes(message, file_path.name, payload)

    def _attach_bytes(self, message: EmailMessage, filename: str, data: bytes) -> None:
        guessed_type, _ = mimetypes.guess_type(filename)
        maintype, _, subtype = (guessed_type or DEFAULT_ATTACHMENT_MIME_TYPE).partition(
            "/"
        )
        message.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=filename,
        )

    async def _send(self, message: EmailMessage) -> None:
        # An empty password means the target relay takes no authentication (local
        # catchers such as Mailpit); passing blank credentials would fail the AUTH
        # handshake instead of skipping it.
        authenticated = bool(self._config.EMAIL_USER and self._config.EMAIL_PASSWORD)

        await aiosmtplib.send(
            message,
            hostname=self._config.EMAIL_SERVER,
            port=self._config.EMAIL_PORT,
            username=self._config.EMAIL_USER if authenticated else None,
            password=self._config.EMAIL_PASSWORD if authenticated else None,
            use_tls=self._config.EMAIL_USE_TLS,
            start_tls=self._config.EMAIL_STARTTLS,
            validate_certs=self._config.VALIDATE_CERTS,
        )
