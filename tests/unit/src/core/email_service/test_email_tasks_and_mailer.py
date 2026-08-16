import logging
from unittest.mock import AsyncMock

import pytest

from src.core.email_service import tasks
from src.core.email_service.enums import MessageType
from src.core.email_service.smtp_mailer import SmtpMailer
from src.core.email_service.tasks import (
    send_email_task,
    send_email_with_s3_attachments_task,
)
from src.core.errors.exceptions import InfrastructureException
from src.main.config import config
from tests.fakes.email import MockMailer
from tests.fakes.s3 import InMemoryS3Client
from tests.helpers.providers import ProvideValue


class FakeS3AdapterContext:
    """Wraps a bare fake S3 client with the async-context-manager protocol
    `build_s3_adapter` normally returns, since `InMemoryS3Client` itself doesn't
    implement `__aenter__`/`__aexit__`."""

    def __init__(self, client: InMemoryS3Client) -> None:
        self._client = client

    async def __aenter__(self) -> InMemoryS3Client:
        return self._client

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_send_email_task_calls_mailer(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    await send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")

    assert len(mock_mailer.sent_template_emails) == 1
    payload = mock_mailer.sent_template_emails[0]
    assert payload["subject"] == "Subj"
    assert payload["recipients"] == ["a@b.com"]
    assert payload["template_name"] == "tpl.html"
    assert payload["template_data"] == {"k": "v"}
    assert payload["subtype"] is MessageType.HTML


@pytest.mark.asyncio
async def test_send_email_with_s3_attachments_task_calls_mailer(
    mock_mailer: MockMailer,
    fake_s3: InMemoryS3Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.s3, "S3_ENABLED", True)
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))
    monkeypatch.setattr(
        tasks, "build_s3_adapter", lambda s3_config: FakeS3AdapterContext(fake_s3)
    )
    await fake_s3.upload_bytes("outbox/2026/report.pdf", b"pdf-bytes")

    await send_email_with_s3_attachments_task(
        "S", ["a@b.com"], ["outbox/2026/report.pdf"], "plain"
    )

    assert len(mock_mailer.sent_attachment_bytes) == 1
    payload = mock_mailer.sent_attachment_bytes[0]
    assert payload["attachments"] == [("report.pdf", b"pdf-bytes")]
    # cleanup defaults to False: the object stays in the bucket after a successful send.
    assert await fake_s3.object_exists("outbox/2026/report.pdf")


@pytest.mark.asyncio
async def test_send_email_with_s3_attachments_task_cleanup_true_deletes_keys(
    mock_mailer: MockMailer,
    fake_s3: InMemoryS3Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.s3, "S3_ENABLED", True)
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))
    monkeypatch.setattr(
        tasks, "build_s3_adapter", lambda s3_config: FakeS3AdapterContext(fake_s3)
    )
    await fake_s3.upload_bytes("outbox/report.pdf", b"pdf-bytes")

    await send_email_with_s3_attachments_task(
        "S", ["a@b.com"], ["outbox/report.pdf"], "plain", cleanup=True
    )

    assert len(mock_mailer.sent_attachment_bytes) == 1
    assert not await fake_s3.object_exists("outbox/report.pdf")


@pytest.mark.asyncio
async def test_send_email_with_s3_attachments_task_failure_keeps_keys(
    fake_s3: InMemoryS3Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.s3, "S3_ENABLED", True)
    failing_mailer = MockMailer()
    monkeypatch.setattr(
        failing_mailer,
        "send_with_attachment_bytes",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(failing_mailer))
    monkeypatch.setattr(
        tasks, "build_s3_adapter", lambda s3_config: FakeS3AdapterContext(fake_s3)
    )
    await fake_s3.upload_bytes("outbox/report.pdf", b"pdf-bytes")

    with pytest.raises(RuntimeError, match="send failed"):
        await send_email_with_s3_attachments_task(
            "S", ["a@b.com"], ["outbox/report.pdf"], "plain", cleanup=True
        )

    # A retry must be able to re-download and re-send the same key.
    assert await fake_s3.object_exists("outbox/report.pdf")


@pytest.mark.asyncio
async def test_send_email_with_s3_attachments_task_cleanup_failure_does_not_resend(
    mock_mailer: MockMailer,
    fake_s3: InMemoryS3Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The production logger is propagate=False (stdout-only); swap in a plain
    # logger so caplog can observe the warning, mirroring test_route_logging.py.
    test_logger = logging.getLogger("email_tasks_cleanup_test")
    test_logger.handlers = []
    test_logger.setLevel(logging.WARNING)
    test_logger.propagate = True
    monkeypatch.setattr(tasks, "logger", test_logger)

    monkeypatch.setattr(config.s3, "S3_ENABLED", True)
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))
    monkeypatch.setattr(
        tasks, "build_s3_adapter", lambda s3_config: FakeS3AdapterContext(fake_s3)
    )
    monkeypatch.setattr(
        fake_s3, "delete_object", AsyncMock(side_effect=RuntimeError("delete failed"))
    )
    await fake_s3.upload_bytes("outbox/report.pdf", b"pdf-bytes")
    caplog.set_level(logging.WARNING, logger="email_tasks_cleanup_test")

    # Must not raise: a post-send cleanup failure would otherwise trigger
    # SmartRetryMiddleware and resend an email that already went out.
    await send_email_with_s3_attachments_task(
        "S", ["a@b.com"], ["outbox/report.pdf"], "plain", cleanup=True
    )

    assert len(mock_mailer.sent_attachment_bytes) == 1
    assert await fake_s3.object_exists("outbox/report.pdf")
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("outbox/report.pdf" in message for message in warnings)


@pytest.mark.asyncio
async def test_send_email_with_s3_attachments_task_raises_when_s3_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.s3, "S3_ENABLED", False)

    with pytest.raises(InfrastructureException):
        await send_email_with_s3_attachments_task(
            "S", ["a@b.com"], ["outbox/report.pdf"], "plain"
        )


def test_get_mailer_builds_smtp_mailer() -> None:
    assert isinstance(tasks.get_mailer(), SmtpMailer)


@pytest.mark.asyncio
async def test_email_tasks_propagate_exceptions(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mock_mailer, "send_template", AsyncMock(side_effect=RuntimeError("fail"))
    )
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    with pytest.raises(RuntimeError):
        await send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")


def test_email_task_registration() -> None:
    assert send_email_task.task_name == "send_email"
    assert (
        send_email_with_s3_attachments_task.task_name
        == "send_email_with_s3_attachments"
    )
    assert send_email_task.labels["retry_on_error"] is True
    assert send_email_with_s3_attachments_task.labels["retry_on_error"] is True
