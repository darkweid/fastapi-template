from pathlib import Path

from pydantic import BaseModel

from src.core.email_service.interfaces import AbstractMailer


class MockMailer(AbstractMailer):
    def __init__(self):
        self.sent_template_emails = []
        self.sent_attachments = []

    async def send_template(
        self,
        subject: str,
        recipients: list[str],
        template_name: str,
        template_data: BaseModel,
        subtype: str = "html",
    ) -> None:
        self.sent_template_emails.append(
            {
                "subject": subject,
                "recipients": recipients,
                "template_name": template_name,
                "template_data": template_data.model_dump(),
                "subtype": subtype,
            }
        )

    async def send_with_attachments(
        self,
        subject: str,
        recipients: list[str],
        body_text: str,
        file_paths: list[Path],
        subtype: str = "plain",
    ) -> None:
        self.sent_attachments.append(
            {
                "subject": subject,
                "recipients": recipients,
                "body_text": body_text,
                "file_paths": file_paths,
                "subtype": subtype,
            }
        )
