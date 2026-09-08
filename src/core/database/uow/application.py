from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.repositories import BaseRepository
from src.core.database.uow.sqlalchemy import RepositoryInstance, SQLAlchemyUnitOfWork
from src.core.outbox.repositories import OutboxRepository
from src.note.repositories import NoteRepository
from src.user.repositories import UserRepository


class ApplicationUnitOfWork(SQLAlchemyUnitOfWork):
    """The application's UoW: every repository, reachable as a property."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self._repositories: dict[type[BaseRepository[Any]], BaseRepository[Any]] = {}

    def _get_repository(
        self, repository_type: type[RepositoryInstance]
    ) -> RepositoryInstance:
        # Repositories are stateless; one instance per type per UoW is enough.
        if repository_type not in self._repositories:
            self._repositories[repository_type] = repository_type()

        return cast(RepositoryInstance, self._repositories[repository_type])

    @property
    def users(self) -> UserRepository:
        return self._get_repository(UserRepository)

    @property
    def outbox(self) -> OutboxRepository:
        return self._get_repository(OutboxRepository)

    @property
    def notes(self) -> NoteRepository:
        return self._get_repository(NoteRepository)


async def get_uow(session: AsyncSession) -> ApplicationUnitOfWork:
    """Build a UoW around an already-created session (tasks, scripts, DI)."""
    return ApplicationUnitOfWork(session)
