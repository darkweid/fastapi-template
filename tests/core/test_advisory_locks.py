from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.database import repositories as repositories_module, transactions
from src.core.database.repositories import BaseRepository


class DummyModel:
    __tablename__ = "dummy"


class DummyRepository(BaseRepository[DummyModel]):
    model = DummyModel


@pytest.mark.asyncio
async def test_try_xact_lock_namespaces_and_returns_bool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = DummyRepository()
    session = MagicMock()

    lock_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(repositories_module, "try_advisory_xact_lock", lock_mock)

    result = await repo.try_xact_lock(session, "order:1")

    assert result is True
    lock_mock.assert_awaited_once_with(session, "dummy:order:1")


@pytest.mark.asyncio
async def test_try_advisory_xact_lock_requires_transaction() -> None:
    session = MagicMock()
    session.in_transaction.return_value = False

    with pytest.raises(RuntimeError):
        await transactions.try_advisory_xact_lock(session, "any")
