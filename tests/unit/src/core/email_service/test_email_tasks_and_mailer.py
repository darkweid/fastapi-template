from unittest.mock import AsyncMock

import pytest

from src.core.email_service import tasks
from src.core.email_service.enums import MessageType
from src.core.email_service.smtp_mailer import SmtpMailer
from src.core.email_service.tasks import send_email_task, send_email_with_file_task
from tests.fakes.email import MockMailer
from tests.helpers.providers import ProvideValue


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
async def test_send_email_with_file_task_calls_mailer(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    await send_email_with_file_task("S", ["a@b.com"], ["/tmp/a.txt"], "plain")

    assert len(mock_mailer.sent_attachments) == 1


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
    assert send_email_with_file_task.task_name == "send_email_with_file"
    assert send_email_task.labels["retry_on_error"] is True
