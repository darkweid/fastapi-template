from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.schemas import SuccessResponse
from src.user.auth.schemas import UserNewPassword
from src.user.usecases.update_password import UpdateUserPasswordUseCase
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


class FakeUsersRepository:
    def __init__(self, updated_user):
        self.update = AsyncMock(return_value=updated_user)


def build_uow(
    session: FakeAsyncSession, users_repo: FakeUsersRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


@pytest.mark.asyncio
async def test_update_password_user_not_found(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
) -> None:
    users_repo = FakeUsersRepository(updated_user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis)

    result = await use_case.execute(
        data=UserNewPassword(password="StrongPass1!"),
        user_id=build_user().id,
    )

    assert result == SuccessResponse(success=False)
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_password_success(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.usecases.update_password.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = UpdateUserPasswordUseCase(uow=uow, redis_client=fake_redis)
    result = await use_case.execute(
        data=UserNewPassword(password="StrongPass1!"),
        user_id=user.id,
    )

    assert result == SuccessResponse(success=True)
    uow.commit.assert_awaited_once()
    invalidate_mock.assert_awaited_once_with(str(user.id), fake_redis)
