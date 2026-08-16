from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.core.email_service.enums import MessageType


class AbstractMailer(ABC):
    @abstractmethod
    async def send_template(
        self,
        subject: str,
        recipients: list[str],
        template_name: str,
        template_data: BaseModel | dict[str, Any],
        subtype: MessageType = MessageType.HTML,
    ) -> None:
        """Send an email based on a template with dynamic content."""

    @abstractmethod
    async def send_with_attachments(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        file_paths: list[Path],
        subtype: MessageType = MessageType.PLAIN,
    ) -> None:
        """Send an email with multiple file attachments read from disk."""

    @abstractmethod
    async def send_with_attachment_bytes(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        attachments: list[tuple[str, bytes]],
        subtype: MessageType = MessageType.PLAIN,
    ) -> None:
        """Send an email with attachments already held in memory as (filename, data)."""
