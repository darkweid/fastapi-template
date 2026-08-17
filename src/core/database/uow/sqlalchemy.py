from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Self, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from loggers import get_logger
from src.core.database.repositories import BaseRepository
from src.core.database.uow.abstract import UnitOfWork

RepositoryInstance = TypeVar("RepositoryInstance", bound=BaseRepository[Any])

logger = get_logger(__name__)

AfterCommitHook = Callable[[], Awaitable[None]]


class SQLAlchemyUnitOfWork(UnitOfWork):
    """
    SQLAlchemy implementation of the Unit of Work pattern.

    This implementation uses SQLAlchemy's AsyncSession for transaction management
    and allows registration of repositories.

    Commit is strictly explicit: leaving the context without calling commit()
    rolls the transaction back, whether the block raised or returned normally.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the UnitOfWork with an SQLAlchemy session.

        Args:
            session: The SQLAlchemy AsyncSession to use for database operations
        """
        self._session = session
        self._transaction: AsyncSessionTransaction | None = None
        self._is_completed = False
        self._after_commit_hooks: list[AfterCommitHook] = []

    async def __aenter__(self) -> Self:
        """
        Enter the context manager and start a transaction.

        Keeps a handle on the transaction it opened (a SAVEPOINT when the
        session is already in one - the normal case for authenticated requests,
        where the auth dependency's SELECT has autobegun on the shared request
        session), so rollback can target exactly this UoW's scope instead of
        the whole session transaction.

        Returns:
            self: The UnitOfWork instance
        """
        if self._session.in_transaction():
            self._transaction = await self._session.begin_nested()
        else:
            self._transaction = await self._session.begin()
        self._session.info["uow_active"] = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Exit the context manager.

        Any exit without a prior commit() rolls back. Without this, a clean
        exit's outcome would depend on invisible context: a fresh session's
        `begin()` block commits on clean exit, while a shared request session
        (every authenticated route) releases the SAVEPOINT and discards the
        work later at session close.

        Args:
            exc_type: Exception type if an exception was raised
            exc_val: Exception value if an exception was raised
            exc_tb: Exception traceback if an exception was raised
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
            self._transaction = None
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
        """
        Commit the transaction.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        self._ensure_not_completed()
        await self._session.commit()
        self._is_completed = True
        await self._run_after_commit_hooks()

    async def rollback(self) -> None:
        """
        Rollback this UoW's transaction scope.

        Rolls back only the transaction this UoW opened: on the nested path
        that is the SAVEPOINT, so uncommitted work the caller staged on the
        shared session before entering the UoW survives. Without the handle
        (rollback outside the context), the whole session transaction is
        rolled back.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        self._ensure_not_completed()
        if self._transaction is not None:
            await self._transaction.rollback()
        else:
            await self._session.rollback()
        self._is_completed = True
        self._after_commit_hooks = []

    async def flush(self) -> None:
        """Flush pending changes to the database."""
        self._ensure_not_completed()
        await self._session.flush()

    async def refresh(
        self,
        instance: Any,
        attribute_names: Sequence[str] | None = None,
        with_for_update: Any | None = None,
    ) -> None:
        """Refresh an ORM instance from the database."""
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
        """
        Check if the unit of work has been completed (committed or rolled back).

        Returns:
            bool: True if the unit of work has been completed, False otherwise
        """
        return self._is_completed

    @property
    def session(self) -> AsyncSession:
        """
        Get the underlying SQLAlchemy session.

        Returns:
            AsyncSession: The SQLAlchemy session
        """
        return self._session
