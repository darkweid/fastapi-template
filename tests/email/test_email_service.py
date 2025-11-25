from pathlib import Path

import pytest

from src.core.email_service.schemas import MailTemplateBodyFile, MailTemplateDataBody
from src.core.email_service.service import EmailService
from tests.email.mocks import MockMailer


@pytest.fixture
def mock_mailer() -> MockMailer:
    return MockMailer()


@pytest.fixture
def email_service(mock_mailer) -> EmailService:
    return EmailService(mock_mailer)


@pytest.mark.asyncio
async def test_send_template_email_valid(
    email_service: EmailService, mock_mailer: MockMailer
):
    body = MailTemplateDataBody(title="Welcome", link="https://example.com")

    await email_service.send_template_email(
        subject="Welcome",
        recipients=["user@example.com"],
        template_name="welcome.html",
        template_body=body,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    assert mock_mailer.sent_template_emails[0]["recipients"] == ["user@example.com"]
    assert mock_mailer.sent_template_emails[0]["template_data"] == body.model_dump()


@pytest.mark.asyncio
async def test_send_template_email_with_invalid_and_valid_emails(
    email_service: EmailService, mock_mailer: MockMailer
):
    body = MailTemplateDataBody(title="Info", link="https://site")

    await email_service.send_template_email(
        subject="Mixed Emails",
        recipients=["valid@example.com", "invalid-email", "also@valid.com"],
        template_name="info.html",
        template_body=body,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    recipients = mock_mailer.sent_template_emails[0]["recipients"]
    assert "valid@example.com" in recipients
    assert "also@valid.com" in recipients
    assert "invalid-email" not in recipients


@pytest.mark.asyncio
async def test_send_template_email_all_invalid(email_service: EmailService):
    with pytest.raises(ValueError, match="No valid recipient emails provided."):
        await email_service.send_template_email(
            subject="None valid",
            recipients=["bad-email", "another-bad"],
            template_name="nope.html",
            template_body=MailTemplateDataBody(title="Bad", link="bad"),
        )


@pytest.mark.asyncio
async def test_send_email_with_single_attachment(
    tmp_path: Path, email_service: EmailService, mock_mailer: MockMailer
):
    file_path = tmp_path / "document.txt"
    file_path.write_text("Attachment content")

    await email_service.send_email_with_single_attachment(
        subject="Attached",
        recipients="user@example.com",
        body_text="Please find the attachment.",
        file_path=file_path,
    )

    assert len(mock_mailer.sent_attachments) == 1
    data = mock_mailer.sent_attachments[0]
    assert data["recipients"] == ["user@example.com"]
    assert data["file_paths"] == [file_path]
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_send_email_with_attachment_all_invalid(
    email_service: EmailService, tmp_path: Path
):
    file_path = tmp_path / "file.txt"
    file_path.write_text("data")

    with pytest.raises(ValueError):
        await email_service.send_email_with_single_attachment(
            subject="Error",
            recipients=["nope", "wrong"],
            body_text="text",
            file_path=file_path,
        )

    assert not file_path.exists()


@pytest.mark.asyncio
async def test_send_email_with_multiple_attachments(
    tmp_path: Path, email_service: EmailService, mock_mailer: MockMailer
):
    file1 = tmp_path / "doc1.pdf"
    file2 = tmp_path / "doc2.csv"
    file1.write_text("PDF content")
    file2.write_text("CSV content")

    await email_service.send_email_with_attachments(
        subject="Multiple Files",
        recipients="user@example.com",
        body_text="Here are multiple attachments.",
        file_paths=[file1, file2],
    )

    assert len(mock_mailer.sent_attachments) == 1
    data = mock_mailer.sent_attachments[0]
    assert set(data["file_paths"]) == {file1, file2}
    assert all(not p.exists() for p in [file1, file2])


@pytest.mark.asyncio
async def test_send_template_email_recipient_as_string(
    email_service: EmailService, mock_mailer: MockMailer
):
    body = MailTemplateBodyFile(title="Report", file="report.pdf")

    await email_service.send_template_email(
        subject="Single recipient as string",
        recipients="string@example.com",
        template_name="template.html",
        template_body=body,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    assert mock_mailer.sent_template_emails[0]["recipients"] == ["string@example.com"]


@pytest.mark.asyncio
async def test_send_email_with_attachment_unusual_file_types(
    tmp_path: Path, email_service: EmailService, mock_mailer: MockMailer
):
    file_path = tmp_path / "strange_type.xyz"
    file_path.write_text("Content of an unknown type")

    await email_service.send_email_with_single_attachment(
        subject="Unusual File",
        recipients="user@example.com",
        body_text="Here is a file with an unusual extension.",
        file_path=file_path,
    )

    assert len(mock_mailer.sent_attachments) == 1
    assert not file_path.exists()


@pytest.mark.asyncio
async def test_send_template_email_empty_recipients(email_service: EmailService):
    body = MailTemplateDataBody(title="Empty", link="none")

    with pytest.raises(ValueError, match="No valid recipient emails provided."):
        await email_service.send_template_email(
            subject="Empty",
            recipients=[],
            template_name="empty.html",
            template_body=body,
        )
