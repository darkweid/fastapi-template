from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.query import ListQuery
from src.core.pagination import (
    PaginatedResponse,
    PaginationParams,
    make_paginated_response,
)
from src.event_log.repositories import EventLogRepository
from src.event_log.schemas import EventLogViewModel


class EventLogService:
    """The read side of the log, and deliberately not a `BaseService`.

    `BaseService` would bring inherited `create`/`update`/`delete` that commit
    on their own - a second, unaudited way into an append-only table. The log
    is written through `EventLogRepository.record`, from inside the use case
    whose transaction the row belongs to.
    """

    def __init__(self, repository: EventLogRepository, session: AsyncSession) -> None:
        self.repository = repository
        self.session = session

    async def get_paginated_list(
        self,
        pagination: PaginationParams,
        query: ListQuery | None = None,
        **filters: Any,
    ) -> PaginatedResponse[EventLogViewModel]:
        items, total = await self.repository.get_paginated_list(
            session=self.session,
            page=pagination.page,
            size=pagination.size,
            query=query,
            **filters,
        )
        return make_paginated_response(
            items=items,
            total=total,
            pagination=pagination,
            schema=EventLogViewModel,
        )
