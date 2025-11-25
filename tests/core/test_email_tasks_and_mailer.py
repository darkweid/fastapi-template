from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.email_service import tasks
from src.core.email_service.fastapi_mailer import FastAPIMailer
from src.core.email_service.tasks import send_email_task, send_email_with_file_task


@pytest.fixture()
def mailer_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.send_template = AsyncMock()
    mock.send_with_attachments = AsyncMock()
    return mock


def test_send_email_task_calls_mailer(
    mailer_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", lambda: mailer_mock)

    send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")

    mailer_mock.send_template.assert_called_once_with(
        subject="Subj",
        recipients=["a@b.com"],
        template_name="tpl.html",
        template_data={"k": "v"},
        subtype="html",
    )


def test_send_email_with_file_task_calls_mailer(
    mailer_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks, "get_mailer", lambda: mailer_mock)

    send_email_with_file_task("S", ["a@b.com"], ["/tmp/a.txt"], "plain")

    mailer_mock.send_with_attachments.assert_called_once()


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
    mailer_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailer_mock.send_template.side_effect = RuntimeError("fail")
    monkeypatch.setattr(tasks, "get_mailer", lambda: mailer_mock)

    with pytest.raises(RuntimeError):
        send_email_task("Subj", ["a@b.com"], "tpl.html", {"k": "v"}, "html")
