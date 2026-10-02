from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
import uuid6

from src.core.database.repositories import BaseRepository
from src.core.database.uow.event_subscribers import event_subscribers
from src.core.database.uow.sqlalchemy import RepositoryInstance, SQLAlchemyUnitOfWork
from src.core.outbox.repositories import OutboxRepository
from src.core.utils.datetime_utils import get_utc_now
from src.event_log.actor import Actor
from src.event_log.events import DomainEvent, PublishedEvent
from src.event_log.repositories import EventLogRepository
from src.note.repositories import NoteRepository
from src.user.repositories import UserRepository


class ApplicationUnitOfWork(SQLAlchemyUnitOfWork):
    """The application's UoW: every repository, reachable as a property."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self._repositories: dict[type[BaseRepository[Any]], BaseRepository[Any]] = {}

    async def publish(self, actor: Actor, event: DomainEvent) -> PublishedEvent:
        """Announce that `event` happened, inside this unit of work.

        The journal row is written first and best-effort (its failure is
        reported, never raised); then every subscriber runs, whatever became
        of the row. A subscriber's failure is raised: what it writes (an
        outbox row) is part of the action and must not be lost silently.
        Call before `commit()`: everything lands or rolls back with the action.
        Returns the occurrence, whose `id` the journal row and every
        subscriber share.
        """
        published = PublishedEvent(
            id=uuid6.uuid7(), actor=actor, event=event, occurred_at=get_utc_now()
        )
        if event.journaled:
            await self.event_logs.record(self.session, published)
        for subscriber in event_subscribers():
            await subscriber(self, published)
        return published

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

    @property
    def event_logs(self) -> EventLogRepository:
        return self._get_repository(EventLogRepository)


async def get_uow(session: AsyncSession) -> ApplicationUnitOfWork:
    """Build a UoW around an already-created session (tasks, scripts, DI)."""
    return ApplicationUnitOfWork(session)
