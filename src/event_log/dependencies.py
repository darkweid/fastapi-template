from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.event_log.repositories import EventLogRepository
from src.event_log.services import EventLogService


def get_event_log_repository() -> EventLogRepository:
    return EventLogRepository()


def get_event_log_service(
    repository: Annotated[EventLogRepository, Depends(get_event_log_repository)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EventLogService:
    return EventLogService(repository=repository, session=session)
