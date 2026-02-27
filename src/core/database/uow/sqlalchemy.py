from collections.abc import Sequence
from contextlib import AsyncExitStack
from typing import Any, Self, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.repositories import BaseRepository
from src.core.database.transactions import safe_begin
from src.core.database.uow.abstract import R, UnitOfWork

# Type variable for repository instances
RepositoryInstance = TypeVar("RepositoryInstance", bound=BaseRepository[Any])


class SQLAlchemyUnitOfWork(UnitOfWork[R]):
    """
    SQLAlchemy implementation of the Unit of Work pattern.

    This implementation uses SQLAlchemy's AsyncSession for transaction management
    and allows registration of repositories.

    Generics:
        R: Repository type bound to RepositoryProtocol, to enable type hinting for repositories
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the UnitOfWork with an SQLAlchemy session.

        Args:
            session: The SQLAlchemy AsyncSession to use for database operations
        """
        self._session = session
        self._exit_stack = AsyncExitStack()  # noqa
        self._is_completed = False

    async def __aenter__(self) -> Self:
        """
        Enter the context manager and start a transaction.

        Returns:
            self: The UnitOfWork instance
        """
        await self._exit_stack.__aenter__()
        await self._exit_stack.enter_async_context(safe_begin(self._session))
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Exit the context manager and close the session.

        Args:
            exc_type: Exception type if an exception was raised
            exc_val: Exception value if an exception was raised
            exc_tb: Exception traceback if an exception was raised
        """
        if exc_type is not None and not self._is_completed:
            await self.rollback()

        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)

    def _ensure_not_completed(self) -> None:
        if self._is_completed:
            raise RuntimeError("This unit of work has already been completed")

    async def commit(self) -> None:
        """
        Commit the transaction.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        self._ensure_not_completed()
        await self._session.commit()
        self._is_completed = True

    async def rollback(self) -> None:
        """
        Rollback the transaction.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        self._ensure_not_completed()
        await self._session.rollback()
        self._is_completed = True

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
