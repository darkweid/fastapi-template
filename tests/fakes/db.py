from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


class AsyncTransactionContext:
    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session
        self._was_in_transaction = session.in_transaction()

    async def __aenter__(self) -> AsyncTransactionContext:
        self._session.set_in_transaction(True)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if not self._was_in_transaction:
            self._session.set_in_transaction(False)
        return None


class FakeAsyncSession:
    def __init__(self, in_transaction: bool = False) -> None:
        self._in_transaction = in_transaction
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.execute = AsyncMock()
        self.add = MagicMock()
        self.delete = AsyncMock()

    def in_transaction(self) -> bool:
        return self._in_transaction

    def set_in_transaction(self, value: bool) -> None:
        self._in_transaction = value

    def begin(self) -> AsyncTransactionContext:
        return AsyncTransactionContext(self)

    def begin_nested(self) -> AsyncTransactionContext:
        return AsyncTransactionContext(self)


class FakeUnitOfWork:
    def __init__(
        self,
        session: FakeAsyncSession | None = None,
        repositories: dict[str, Any] | None = None,
    ) -> None:
        self._session = session or FakeAsyncSession()
        self._repositories = repositories or {}
        self._completed = False
        self.commit = AsyncMock(side_effect=self._mark_committed)
        self.rollback = AsyncMock(side_effect=self._mark_rolled_back)

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if exc_type is not None and not self._completed:
            await self.rollback()
        return None

    def _mark_committed(self) -> None:
        self._completed = True

    def _mark_rolled_back(self) -> None:
        self._completed = True

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def session(self) -> FakeAsyncSession:
        return self._session

    def __getattr__(self, name: str) -> Any:
        if name in self._repositories:
            return self._repositories[name]
        raise AttributeError(name)
