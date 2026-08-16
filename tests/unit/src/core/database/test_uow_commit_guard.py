from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.database.uow.application import ApplicationUnitOfWork
from src.core.utils.security import hash_password
from src.user.enums import UserRole
from src.user.repositories import UserRepository
from tests.fakes.db import FakeAsyncSession


def _valid_user_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "first_name": "Test",
        "last_name": "User",
        "email": "user@example.com",
        "username": "user",
        "phone_number": "+10000000000",
        "password_hash": hash_password("password"),
        "role": UserRole.VIEWER,
        "is_verified": True,
        "is_active": True,
    }
    data.update(overrides)
    return data


@pytest.fixture
def session() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def user_repository() -> UserRepository:
    return UserRepository()


async def test_standalone_commit_allowed_even_after_autobegin(
    session: FakeAsyncSession, user_repository: UserRepository
) -> None:
    # a prior read opens a transaction via autobegin; guard must not trip
    session.execute.return_value = MagicMock()
    await user_repository.get_list(session)
    created = await user_repository.create(
        session, data=_valid_user_data(), commit=True
    )
    assert created is not None


async def test_commit_true_inside_uow_raises(
    session: FakeAsyncSession, user_repository: UserRepository
) -> None:
    uow = ApplicationUnitOfWork(session)
    async with uow:
        with pytest.raises(RuntimeError, match="inside an active UnitOfWork"):
            await user_repository.create(session, data=_valid_user_data(), commit=True)
        # the UoW transaction survived the refusal
        await uow.commit()


async def test_commit_false_inside_uow_is_legal(
    session: FakeAsyncSession, user_repository: UserRepository
) -> None:
    async with ApplicationUnitOfWork(session) as uow:
        await user_repository.create(uow.session, data=_valid_user_data(), commit=False)
        await uow.commit()


async def test_marker_cleared_after_uow_exit(session: FakeAsyncSession) -> None:
    async with ApplicationUnitOfWork(session):
        assert session.info.get("uow_active") is True
    assert session.info.get("uow_active") is None


async def test_marker_cleared_when_uow_body_raises(session: FakeAsyncSession) -> None:
    with pytest.raises(ValueError):
        async with ApplicationUnitOfWork(session):
            raise ValueError("boom")
    assert session.info.get("uow_active") is None
