from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.repositories import BaseRepository
from src.core.outbox.enums import OutboxMessageStatus
from src.core.outbox.models import OutboxMessage
from src.core.utils.datetime_utils import get_utc_now

LAST_ERROR_MAX_LENGTH = 2000


class OutboxRepository(BaseRepository[OutboxMessage]):
    model = OutboxMessage

    async def get_batch_for_publish(
        self, session: AsyncSession, limit: int
    ) -> Sequence[OutboxMessage]:
        """Lock a FIFO batch of pending rows; SKIP LOCKED keeps concurrent sweeps disjoint."""
        query = (
            select(self.model)
            .where(self.model.status == OutboxMessageStatus.PENDING)
            .order_by(self.model.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(query)
        return result.scalars().all()

    async def mark_published(self, session: AsyncSession, message_id: UUID) -> None:
        await session.execute(
            update(self.model)
            .where(self.model.id == message_id)
            .values(
                status=OutboxMessageStatus.PUBLISHED,
                published_at=get_utc_now(),
            )
        )

    async def mark_publish_failure(
        self, session: AsyncSession, message_id: UUID, error: str, final: bool
    ) -> None:
        values: dict[str, Any] = {
            "attempts": self.model.attempts + 1,
            "last_error": error[:LAST_ERROR_MAX_LENGTH],
        }
        if final:
            values["status"] = OutboxMessageStatus.FAILED
        await session.execute(
            update(self.model).where(self.model.id == message_id).values(**values)
        )

    async def purge_published(self, session: AsyncSession, cutoff: datetime) -> int:
        result = await session.execute(
            delete(self.model).where(
                self.model.status == OutboxMessageStatus.PUBLISHED,
                self.model.published_at < cutoff,
            )
        )
        return int(result.rowcount) if hasattr(result, "rowcount") else 0
