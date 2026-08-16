from email.message import EmailMessage
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.core.email_service import smtp_mailer as smtp_mailer_module
from src.core.email_service.enums import MessageType
from src.core.email_service.schemas import MailTemplateVerificationBody
from src.core.email_service.smtp_mailer import SmtpMailer
from src.main.config import BroadcastingConfig


def build_config(**overrides: Any) -> BroadcastingConfig:
    values = {
        "EMAIL_SERVER": "smtp.example.com",
        "EMAIL_PORT": 587,
        "EMAIL_PASSWORD": "secret",
        "EMAIL_USER": "sender@example.com",
        "EMAIL_FROM_NAME": "Example App",
        "EMAIL_USE_TLS": False,
        "EMAIL_STARTTLS": True,
        "VALIDATE_CERTS": False,
    }
    values.update(overrides)
    return BroadcastingConfig(**values)


@pytest.fixture
def send_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(smtp_mailer_module.aiosmtplib, "send", mock)
    return mock


def sent_message(send_mock: AsyncMock) -> EmailMessage:
    return send_mock.await_args.args[0]


@pytest.mark.asyncio
async def test_send_template_renders_body_and_headers(send_mock: AsyncMock) -> None:
    mailer = SmtpMailer(build_config())

    await mailer.send_template(
        subject="Verification Message",
        recipients=["first@example.com", "second@example.com"],
        template_name="notification.html",
        template_data={"title": "Notification", "message": "Hello"},
    )

    message = sent_message(send_mock)
    assert message["Subject"] == "Verification Message"
    assert message["From"] == "Example App <sender@example.com>"
    assert message["To"] == "first@example.com, second@example.com"
    assert message["Message-ID"]
    assert message.get_content_type() == "text/html"
    assert "Notification" in message.get_content()


@pytest.mark.asyncio
async def test_send_template_accepts_pydantic_body(send_mock: AsyncMock) -> None:
    mailer = SmtpMailer(build_config())

    await mailer.send_template(
        subject="Verification Message",
        recipients=["first@example.com"],
        template_name="verification.html",
        template_data=MailTemplateVerificationBody(
            title="Verification Message",
            link="https://example.com/verify?token=abc",
            name="Ada",
        ),
    )

    body = sent_message(send_mock).get_content()
    assert "Ada" in body
    assert "https://example.com/verify?token=abc" in body


@pytest.mark.asyncio
async def test_send_template_honours_plain_subtype(send_mock: AsyncMock) -> None:
    mailer = SmtpMailer(build_config())

    await mailer.send_template(
        subject="Plain",
        recipients=["first@example.com"],
        template_name="notification.html",
        template_data={"title": "Notification", "message": "Hello"},
        subtype=MessageType.PLAIN,
    )

    assert sent_message(send_mock).get_content_type() == "text/plain"


@pytest.mark.asyncio
async def test_send_with_attachments_encodes_every_file(
    send_mock: AsyncMock, tmp_path: Path
) -> None:
    text_file = tmp_path / "report.txt"
    text_file.write_text("report body")
    binary_file = tmp_path / "logo.png"
    binary_file.write_bytes(b"\x89PNG\r\n")
    mailer = SmtpMailer(build_config())

    await mailer.send_with_attachments(
        subject="Report",
        recipients=["first@example.com"],
        body_text="See attached",
        file_paths=[text_file, binary_file],
    )

    attachments = list(sent_message(send_mock).iter_attachments())
    assert [part.get_filename() for part in attachments] == ["report.txt", "logo.png"]
    assert attachments[0].get_content_type() == "text/plain"
    assert attachments[1].get_content_type() == "image/png"
    assert attachments[1].get_payload(decode=True) == b"\x89PNG\r\n"


@pytest.mark.asyncio
async def test_send_with_attachment_bytes_encodes_every_attachment(
    send_mock: AsyncMock,
) -> None:
    mailer = SmtpMailer(build_config())

    await mailer.send_with_attachment_bytes(
        subject="Report",
        recipients=["first@example.com"],
        body_text="See attached",
        attachments=[
            ("report.txt", b"report body"),
            ("logo.png", b"\x89PNG\r\n"),
        ],
    )

    attachments = list(sent_message(send_mock).iter_attachments())
    assert [part.get_filename() for part in attachments] == ["report.txt", "logo.png"]
    assert attachments[0].get_content_type() == "text/plain"
    assert attachments[1].get_content_type() == "image/png"
    assert attachments[1].get_payload(decode=True) == b"\x89PNG\r\n"


@pytest.mark.asyncio
async def test_send_passes_transport_settings(send_mock: AsyncMock) -> None:
    mailer = SmtpMailer(build_config(EMAIL_USE_TLS=True, EMAIL_STARTTLS=False))

    await mailer.send_with_attachments(
        subject="Report",
        recipients=["first@example.com"],
        body_text="See attached",
        file_paths=[],
    )

    kwargs = send_mock.await_args.kwargs
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert kwargs["validate_certs"] is False
    assert kwargs["username"] == "sender@example.com"
    assert kwargs["password"] == "secret"


@pytest.mark.asyncio
async def test_send_skips_authentication_without_password(
    send_mock: AsyncMock,
) -> None:
    mailer = SmtpMailer(build_config(EMAIL_PASSWORD=""))

    await mailer.send_with_attachments(
        subject="Report",
        recipients=["first@example.com"],
        body_text="See attached",
        file_paths=[],
    )

    kwargs = send_mock.await_args.kwargs
    assert kwargs["username"] is None
    assert kwargs["password"] is None
