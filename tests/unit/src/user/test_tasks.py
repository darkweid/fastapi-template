from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.core.database.filters import FilterCondition
from src.user.repositories import UserRepository
from src.user.tasks import cleanup_unverified_users
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.helpers.providers import ProvideValue


class FakeUsersRepository:
    def __init__(
        self, result: int | None = None, error: Exception | None = None
    ) -> None:
        if error is not None:
            self.batch_soft_delete = AsyncMock(side_effect=error)
        else:
            self.batch_soft_delete = AsyncMock(return_value=result or 0)


def build_uow(
    session: FakeAsyncSession, users_repo: FakeUsersRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


@pytest.mark.asyncio
async def test_cleanup_unverified_users_success(
    fake_session: FakeAsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    monkeypatch.setattr("src.user.tasks.get_utc_now", ProvideValue(fixed_now))
    users_repo = FakeUsersRepository(result=5)
    uow = build_uow(fake_session, users_repo)

    with patch("src.user.tasks.ApplicationUnitOfWork", return_value=uow):
        result = await cleanup_unverified_users(session=fake_session)

    assert result == "Deleted 5 unverified users."
    users_repo.batch_soft_delete.assert_awaited_once()
    uow.commit.assert_awaited_once()
    uow.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_unverified_users_swallows_and_reports_failure(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(error=Exception("DB Error"))
    uow = build_uow(fake_session, users_repo)

    with patch("src.user.tasks.ApplicationUnitOfWork", return_value=uow):
        result = await cleanup_unverified_users(session=fake_session)

    assert result == "Deleted 0 unverified users."
    uow.rollback.assert_awaited_once()
    assert uow.completed is True


def test_cleanup_task_registration() -> None:
    assert cleanup_unverified_users.task_name == "cleanup_unverified_users"
    assert cleanup_unverified_users.labels["schedule"] == [{"cron": "0 */10 * * *"}]


@pytest.mark.asyncio
async def test_batch_soft_delete_raises_value_error_on_empty_filters(
    fake_session: FakeAsyncSession,
) -> None:
    repo = UserRepository()

    with pytest.raises(
        ValueError, match="At least one filter condition must be provided"
    ):
        await repo.batch_soft_delete(fake_session, filters=FilterCondition())
