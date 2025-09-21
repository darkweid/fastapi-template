from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from typing import Any, Generic, TypeVar, cast, ClassVar, Protocol, Self

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.repositories import BaseRepository
from src.core.database.transactions import safe_begin
from src.user.repositories import UserRepository


class RepositoryProtocol(Protocol):
    """Protocol defining the structure of a repository class."""

    model: ClassVar[Any]


R = TypeVar("R", bound=RepositoryProtocol)


class UnitOfWork(ABC, Generic[R]):
    """
    Abstract Unit of Work interface that defines the contract for concrete UoW implementations.

    The Unit of Work pattern provides an abstraction over the transaction boundary
    and encapsulates all repositories needed for business operations within a single unit.

    Generics:
        R: Repository type bound to RepositoryProtocol, to enable type hinting for repositories
    """

    @abstractmethod
    async def __aenter__(self) -> "UnitOfWork[R]":
        """Enter the context manager, starting a transaction if needed."""
        pass

    @abstractmethod
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context manager, committing or rolling back the transaction."""
        pass

    @abstractmethod
    async def commit(self) -> None:
        """Commit the transaction."""
        pass

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the transaction."""
        pass

    @property
    @abstractmethod
    def completed(self) -> bool:
        """Check if the unit of work has been completed (committed or rolled back)."""
        pass


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
        Initialize the UnitOfWork with a SQLAlchemy session.

        Args:
            session: The SQLAlchemy AsyncSession to use for database operations
        """
        self._session = session
        self._exit_stack = AsyncExitStack()
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

    async def commit(self) -> None:
        """
        Commit the transaction.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        if self._is_completed:
            raise RuntimeError("This unit of work has already been completed")

        await self._session.commit()
        self._is_completed = True

    async def rollback(self) -> None:
        """
        Rollback the transaction.

        Raises:
            RuntimeError: If the unit of work has already been completed
        """
        if self._is_completed:
            raise RuntimeError("This unit of work has already been completed")

        await self._session.rollback()
        self._is_completed = True

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


# Type variable for repository instances
T = TypeVar("T", bound=SQLAlchemyBase)
RepositoryInstance = TypeVar("RepositoryInstance", bound=BaseRepository[Any])


class ApplicationUnitOfWork(SQLAlchemyUnitOfWork[R]):
    """
    Application-specific Unit of Work implementation.

    This class extends SQLAlchemyUnitOfWork and provides repository factory methods
    for all repositories used in the application.
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize the ApplicationUnitOfWork with a SQLAlchemy session.

        Args:
            session: The SQLAlchemy AsyncSession to use for database operations
        """
        super().__init__(session)
        self._repositories: dict[type[BaseRepository[Any]], BaseRepository[Any]] = {}

    def _get_repository(
        self, repository_type: type[RepositoryInstance]
    ) -> RepositoryInstance:
        """
        Get or create a repository of the specified type.

        This method implements a caching mechanism for repositories
        to avoid creating multiple instances of the same repository.

        Args:
            repository_type: The repository class to get or create

        Returns:
            An instance of the specified repository type
        """
        if repository_type not in self._repositories:
            self._repositories[repository_type] = repository_type()

        return cast(RepositoryInstance, self._repositories[repository_type])

    @property
    def users(self) -> UserRepository:
        """
        Get the UserRepository.

        Returns:
            UserRepository: The user repository
        """
        return self._get_repository(UserRepository)

    # Add more repository properties as needed


async def get_uow(session: AsyncSession) -> ApplicationUnitOfWork[RepositoryProtocol]:
    """
    Dependency injection function to get an ApplicationUnitOfWork instance.

    Args:
        session: The SQLAlchemy AsyncSession to use for database operations

    Returns:
        ApplicationUnitOfWork: The UnitOfWork instance
    """
    return ApplicationUnitOfWork(session)
