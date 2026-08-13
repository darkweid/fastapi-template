from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.core.outbox import tasks as outbox_tasks
from src.core.outbox.tasks import (
    MAX_PUBLISH_ATTEMPTS,
    outbox_purge,
    outbox_sweeper,
)
from src.core.utils.datetime_utils import get_utc_now
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork


def make_message(task_name: str = "outbox_probe", attempts: int = 0) -> MagicMock:
    message = MagicMock()
    message.id = uuid4()
    message.task_name = task_name
    message.args = ["hello"]
    message.kwargs = {"flag": True}
    message.attempts = attempts
    return message


def make_uow(messages: list) -> tuple[FakeUnitOfWork, AsyncMock]:
    outbox_repo = AsyncMock()
    outbox_repo.get_batch_for_publish = AsyncMock(return_value=messages)
    uow = FakeUnitOfWork(repositories={"outbox": outbox_repo})
    return uow, outbox_repo


def patch_uow(uow: FakeUnitOfWork):
    return patch.object(outbox_tasks, "ApplicationUnitOfWork", return_value=uow)


async def test_sweeper_publishes_pending_with_row_id_as_task_id() -> None:
    message = make_message()
    uow, repo = make_uow([message])
    kicker = MagicMock()
    kicker.with_task_id.return_value.kiq = AsyncMock()
    task = MagicMock()
    task.kicker.return_value = kicker

    with patch_uow(uow), patch.object(
        outbox_tasks.broker, "find_task", return_value=task
    ):
        result = await outbox_sweeper(session=FakeAsyncSession())

    kicker.with_task_id.assert_called_once_with(str(message.id))
    kicker.with_task_id.return_value.kiq.assert_awaited_once_with("hello", flag=True)
    repo.mark_published.assert_awaited_once()
    assert result == "Published 1, failed 0."


async def test_sweeper_increments_attempts_on_publish_error() -> None:
    message = make_message(attempts=0)
    uow, repo = make_uow([message])
    kicker = MagicMock()
    kicker.with_task_id.return_value.kiq = AsyncMock(side_effect=RuntimeError("down"))
    task = MagicMock()
    task.kicker.return_value = kicker

    with patch_uow(uow), patch.object(
        outbox_tasks.broker, "find_task", return_value=task
    ):
        await outbox_sweeper(session=FakeAsyncSession())

    repo.mark_publish_failure.assert_awaited_once()
    assert repo.mark_publish_failure.await_args.kwargs["final"] is False
    repo.mark_published.assert_not_awaited()


async def test_sweeper_marks_failed_after_max_attempts() -> None:
    message = make_message(attempts=MAX_PUBLISH_ATTEMPTS - 1)
    uow, repo = make_uow([message])
    kicker = MagicMock()
    kicker.with_task_id.return_value.kiq = AsyncMock(side_effect=RuntimeError("down"))
    task = MagicMock()
    task.kicker.return_value = kicker

    with patch_uow(uow), patch.object(
        outbox_tasks.broker, "find_task", return_value=task
    ), patch.object(outbox_tasks, "_report_failed_message") as report:
        await outbox_sweeper(session=FakeAsyncSession())

    assert repo.mark_publish_failure.await_args.kwargs["final"] is True
    report.assert_called_once()


async def test_sweeper_fails_unregistered_task_immediately() -> None:
    message = make_message(task_name="renamed_task")
    uow, repo = make_uow([message])

    with patch_uow(uow), patch.object(
        outbox_tasks.broker, "find_task", return_value=None
    ), patch.object(outbox_tasks, "_report_failed_message") as report:
        result = await outbox_sweeper(session=FakeAsyncSession())

    assert repo.mark_publish_failure.await_args.kwargs["final"] is True
    report.assert_called_once()
    assert result == "Published 0, failed 1."


async def test_purge_uses_retention_cutoff() -> None:
    uow, repo = make_uow([])
    repo.purge_published = AsyncMock(return_value=5)
    frozen_now = get_utc_now()

    with patch_uow(uow), patch.object(
        outbox_tasks, "get_utc_now", return_value=frozen_now
    ):
        result = await outbox_purge(session=FakeAsyncSession())

    cutoff = repo.purge_published.await_args.args[1]
    assert cutoff == frozen_now - timedelta(days=7)
    assert result == "Deleted 5 published outbox messages."
