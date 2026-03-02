from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Generic, Protocol, TypeVar


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

    @abstractmethod
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context manager, committing or rolling back the transaction."""

    @abstractmethod
    async def commit(self) -> None:
        """Commit the transaction."""

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback the transaction."""

    @abstractmethod
    async def flush(self) -> None:
        """Flush pending changes within the current transaction."""

    @abstractmethod
    async def refresh(
        self,
        instance: Any,
        attribute_names: Sequence[str] | None = None,
        with_for_update: Any | None = None,
    ) -> None:
        """Refresh an instance from the database."""

    @property
    @abstractmethod
    def completed(self) -> bool:
        """Check if the unit of work has been completed (committed or rolled back)."""
