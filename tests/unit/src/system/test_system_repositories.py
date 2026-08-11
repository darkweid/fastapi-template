from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.system.repositories import SystemRepository
from tests.fakes.db import FakeAsyncSession


@pytest.mark.asyncio
async def test_ping_executes_a_query(fake_session: FakeAsyncSession) -> None:
    fake_session.execute = AsyncMock()
    repository = SystemRepository()

    await repository.ping(fake_session)

    fake_session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_ping_propagates_database_errors(fake_session: FakeAsyncSession) -> None:
    fake_session.execute = AsyncMock(side_effect=SQLAlchemyError("down"))
    repository = SystemRepository()

    with pytest.raises(SQLAlchemyError):
        await repository.ping(fake_session)
