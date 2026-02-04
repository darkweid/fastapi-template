from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.user.services import UserService
from tests.fakes.db import FakeAsyncSession


class FakeRepository:
    def __init__(self, user) -> None:
        self.get_single = AsyncMock(return_value=user)
        self.model = type("UserModel", (), {})


@pytest.mark.asyncio
async def test_user_service_get_single(fake_session: FakeAsyncSession) -> None:
    user = object()
    repo = FakeRepository(user)
    service = UserService(repository=repo)

    result = await service.get_single(fake_session, id="user-id")

    assert result is user
    repo.get_single.assert_awaited_once()
