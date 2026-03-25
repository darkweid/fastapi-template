from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.schemas import Base
from src.user.services import UserService
from tests.fakes.db import FakeAsyncSession


class FakeRepository:
    def __init__(self, user) -> None:
        self.create = AsyncMock(return_value=user)
        self.get_single = AsyncMock(return_value=user)
        self.update = AsyncMock(return_value=user)
        self.model = type("UserModel", (), {})


class CreateSchema(Base):
    email: str
    full_name: str


class UpdateSchema(Base):
    email: str | None = None
    full_name: str | None = None


@pytest.mark.asyncio
async def test_user_service_create_passes_full_payload(
    fake_session: FakeAsyncSession,
) -> None:
    user = object()
    repo = FakeRepository(user)
    service = UserService(repository=repo)
    payload = CreateSchema(email="test@example.com", full_name="Test User")

    result = await service.create(fake_session, payload)

    assert result is user
    repo.create.assert_awaited_once_with(
        session=fake_session,
        data={"email": "test@example.com", "full_name": "Test User"},
        commit=True,
    )


@pytest.mark.asyncio
async def test_user_service_get_single(fake_session: FakeAsyncSession) -> None:
    user = object()
    repo = FakeRepository(user)
    service = UserService(repository=repo)

    result = await service.get_single(fake_session, id="user-id")

    assert result is user
    repo.get_single.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_service_update_uses_partial_model_dump(
    fake_session: FakeAsyncSession,
) -> None:
    user = object()
    repo = FakeRepository(user)
    service = UserService(repository=repo)
    payload = UpdateSchema(full_name="Updated User")

    result = await service.update(fake_session, payload, id="user-id")

    assert result is user
    repo.update.assert_awaited_once_with(
        session=fake_session,
        data={"full_name": "Updated User"},
        id="user-id",
        commit=True,
    )
