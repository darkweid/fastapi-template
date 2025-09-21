from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class AbstractMailer(ABC):
    @abstractmethod
    async def send_template(
        self,
        subject: str,
        recipients: list[str],
        template_name: str,
        template_data: BaseModel | dict[str, Any],
        subtype: str = "html",
    ) -> None:
        """Send an email based on a template with dynamic content."""
        pass

    @abstractmethod
    async def send_with_attachments(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        file_paths: list[Path],
        subtype: str = "plain",
    ) -> None:
        """Send an email with multiple file attachments."""
        pass
