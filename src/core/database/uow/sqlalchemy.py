from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Self, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger
from src.core.database.repositories import BaseRepository

RepositoryInstance = TypeVar("RepositoryInstance", bound=BaseRepository[Any])

logger = get_logger(__name__)

AfterCommitHook = Callable[[], Awaitable[None]]


class SQLAlchemyUnitOfWork:
    """
    Transaction boundary over an AsyncSession, shared by every UseCase.

    A unit of work is always the top-level transaction: entering one on a
    session that is already in a transaction raises. Commit is strictly
    explicit: leaving the context without calling commit() rolls the
    transaction back, whether the block raised or returned normally. Any later
    `uow.*` call raises RuntimeError.
    """

    def __init__(self, session: AsyncSession):
        self._session = session
        self._is_completed = False
        self._after_commit_hooks: list[AfterCommitHook] = []

    async def __aenter__(self) -> Self:
        """
        Begin the session's transaction.

        Raises RuntimeError when the session is already in one - an earlier
        read on the shared session autobegins a transaction, and so does an
        enclosing unit of work. A SAVEPOINT there would make `commit()` commit
        that outer transaction too, work this unit never saw included. Nothing
        is touched on refusal: the session stays in its transaction.
        """
        if self._session.in_transaction():
            raise RuntimeError(
                "UnitOfWork entered while the session is already in a "
                "transaction. Read through `detached_read` or move the read "
                "inside the UnitOfWork; a UnitOfWork is never nested."
            )
        await self._session.begin()
        self._session.info["uow_active"] = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Roll back on any exit without a prior commit().

        Without this, a clean exit would leave the transaction open on the
        session until it closes, and its outcome would depend on what the
        caller did next.
        """
        try:
            if not self._is_completed:
                if exc_type is None and self._has_pending_changes():
                    logger.warning(
                        "UnitOfWork exited cleanly without commit() while "
                        "changes were pending; rolling back."
                    )
                await self.rollback()
        finally:
            self._session.info.pop("uow_active", None)

    def _has_pending_changes(self) -> bool:
        # Best-effort forgotten-commit detection: already-flushed changes are
        # not visible in these collections, so silence proves nothing.
        return bool(self._session.dirty or self._session.new or self._session.deleted)

    def _ensure_not_completed(self) -> None:
        if self._is_completed:
            raise RuntimeError("This unit of work has already been completed")

    def add_after_commit_hook(self, hook: AfterCommitHook) -> None:
        """Register a coroutine to run once after a successful commit."""
        self._ensure_not_completed()
        self._after_commit_hooks.append(hook)

    async def commit(self) -> None:
        self._ensure_not_completed()
        await self._session.commit()
        self._is_completed = True
        await self._run_after_commit_hooks()

    async def rollback(self) -> None:
        self._ensure_not_completed()
        await self._session.rollback()
        self._is_completed = True
        self._after_commit_hooks = []

    async def flush(self) -> None:
        self._ensure_not_completed()
        await self._session.flush()

    async def refresh(
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

    async def _run_after_commit_hooks(self) -> None:
        hooks, self._after_commit_hooks = self._after_commit_hooks, []
        for hook in hooks:
            # Best-effort by design: the data is committed and the outbox
            # sweeper guarantees delivery, so a failed hook must not fail the
            # request. This is the one sanctioned exception swallow.
            try:
                await hook()
            except Exception:
                logger.exception("After-commit hook failed")

    @property
    def completed(self) -> bool:
        """True once the unit of work has been committed or rolled back."""
        return self._is_completed

    @property
    def session(self) -> AsyncSession:
        return self._session
