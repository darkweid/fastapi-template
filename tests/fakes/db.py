from __future__ import annotations

from collections.abc import Generator, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tests.fakes.event_log import FakeEventLogRepository


class AsyncTransactionContext:
    """Stands in for AsyncSessionTransaction: awaitable like the real
    `session.begin()` / `begin_nested()`, usable as an async CM."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session
        self._was_in_transaction = session.in_transaction()
        self.nested = self._was_in_transaction

    async def start(self) -> AsyncTransactionContext:
        self._session.set_in_transaction(True)
        return self

    def __await__(self) -> Generator[Any, None, AsyncTransactionContext]:
        return self.start().__await__()

    async def __aenter__(self) -> AsyncTransactionContext:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if not self._was_in_transaction:
            self._session.set_in_transaction(False)
        if exc_type is None and self._session.fail_nested_with is not None:
            error, self._session.fail_nested_with = self._session.fail_nested_with, None
            raise error
        return None

    async def rollback(self) -> None:
        # Forward to the session mock so tests can keep asserting on
        # `session.rollback`; scope granularity is proven at integration level.
        await self._session.rollback()
        if not self._was_in_transaction:
            self._session.set_in_transaction(False)


class FakeAsyncSession:
    def __init__(self, in_transaction: bool = False) -> None:
        self._in_transaction = in_transaction
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.execute = AsyncMock()
        # Rows handed to `add()`, in order: the event log inserts through the
        # session instead of a repository method.
        self.added: list[Any] = []
        # Still a MagicMock: tests assert on `session.add` being called, and a
        # plain method would not record those calls.
        self.add = MagicMock(side_effect=self.added.append)
        self.delete = AsyncMock()
        # When set, the next `begin_nested()` block raises this on a clean exit -
        # the only way to exercise the event log's swallow-and-report path on a
        # fake session.
        self.fail_nested_with: Exception | None = None
        # Mirrors real `AsyncSession.info`: a plain dict the UoW uses to mark
        # itself active for the repository commit guard.
        self.info: dict[str, Any] = {}
        # Mirror the real session's pending-state views, consulted by the UoW's
        # forgotten-commit warning on clean exit.
        self.dirty: set[Any] = set()
        self.new: set[Any] = set()
        self.deleted: set[Any] = set()

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
        self._repositories = dict(repositories or {})
        # Every use case may log; a test that does not care about the event
        # should not have to wire the repository that stores it.
        self._repositories.setdefault("event_logs", FakeEventLogRepository())
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
