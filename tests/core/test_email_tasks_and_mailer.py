from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.email_service import tasks
from src.core.email_service.fastapi_mailer import FastAPIMailer
from src.core.email_service.tasks import send_email_task, send_email_with_file_task
from tests.email.mocks import MockMailer
from tests.helpers.providers import ProvideValue


def test_send_email_task_calls_mailer(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")

    assert len(mock_mailer.sent_template_emails) == 1
    payload = mock_mailer.sent_template_emails[0]
    assert payload["subject"] == "Subj"
    assert payload["recipients"] == ["a@b.com"]
    assert payload["template_name"] == "tpl.html"
    assert payload["template_data"] == {"k": "v"}
    assert payload["subtype"] == "html"


def test_send_email_with_file_task_calls_mailer(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    send_email_with_file_task("S", ["a@b.com"], ["/tmp/a.txt"], "plain")

    assert len(mock_mailer.sent_attachments) == 1


@pytest.mark.asyncio
async def test_fastapi_mailer_wrappers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    send_message_mock = AsyncMock()

    mailer = object.__new__(FastAPIMailer)
    mailer._mailer = MagicMock(send_message=send_message_mock)

    file_path = tmp_path / "a.txt"
    file_path.write_text("content")

    await mailer.send_template("S", ["a@example.com"], "tpl", {"k": "v"}, "html")
    await mailer.send_with_attachments(
        "S", ["a@example.com"], "body", [file_path], "plain"
    )

    assert send_message_mock.await_count == 2
    first_call_args = send_message_mock.await_args_list[0]
    second_call_args = send_message_mock.await_args_list[1]
    assert first_call_args.kwargs["template_name"] == "tpl"
    assert not second_call_args.kwargs  # second call has no template_name kwarg


def test_email_tasks_propagate_exceptions(
    mock_mailer: MockMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mock_mailer, "send_template", AsyncMock(side_effect=RuntimeError("fail"))
    )
    monkeypatch.setattr(tasks, "get_mailer", ProvideValue(mock_mailer))

    with pytest.raises(RuntimeError):
        send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")
