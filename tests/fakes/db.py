from __future__ import annotations

from collections.abc import Sequence
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

    async def __aenter__(self) -> FakeAsyncSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        return None


class FakeSessionFactory:
    """Stands in for async_sessionmaker: calling it yields the same fake session."""

    def __init__(self, session: FakeAsyncSession | None = None) -> None:
        self.session = session or FakeAsyncSession()

    def __call__(self) -> FakeAsyncSession:
        return self.session


class FakeUnitOfWork:
    def __init__(
        self,
        session: FakeAsyncSession | None = None,
        repositories: dict[str, Any] | None = None,
    ) -> None:
        self._session = session or FakeAsyncSession()
        self._repositories = repositories or {}
        self._completed = False
        self._after_commit_hooks: list[Any] = []
        self.commit = AsyncMock(side_effect=self._commit)
        self.rollback = AsyncMock(side_effect=self._mark_rolled_back)
        self.flush = AsyncMock(side_effect=self._flush)
        self.refresh = AsyncMock(side_effect=self._refresh)

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

    async def _commit(self) -> None:
        self._completed = True
        hooks, self._after_commit_hooks = self._after_commit_hooks, []
        for hook in hooks:
            try:
                await hook()
            except Exception:  # noqa: S110 - mirrors the real UoW hook runner
                pass

    def add_after_commit_hook(self, hook: Any) -> None:
        self._ensure_not_completed()
        self._after_commit_hooks.append(hook)

    def _mark_rolled_back(self) -> None:
        self._completed = True
        self._after_commit_hooks = []

    def _ensure_not_completed(self) -> None:
        if self._completed:
            raise RuntimeError("This unit of work has already been completed")

    async def _flush(self) -> None:
        self._ensure_not_completed()
        await self._session.flush()

    async def _refresh(
        self,
        instance: Any,
        attribute_names: Sequence[str] | None = None,
        with_for_update: Any | None = None,
    ) -> None:
        self._ensure_not_completed()
        await self._session.refresh(
            instance,
            attribute_names=attribute_names,
            with_for_update=with_for_update,
        )

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
