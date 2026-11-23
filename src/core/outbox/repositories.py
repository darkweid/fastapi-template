from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.repositories import BaseRepository
from src.core.outbox.enums import OutboxMessageStatus
from src.core.outbox.models import OutboxMessage
from src.core.utils.datetime_utils import get_utc_now

LAST_ERROR_MAX_LENGTH = 2000


class OutboxRepository(BaseRepository[OutboxMessage]):
    model = OutboxMessage

    async def get_batch_for_publish(
        self, session: AsyncSession, limit: int, *, min_age: timedelta
    ) -> Sequence[OutboxMessage]:
        """
        Lock a FIFO batch of pending rows older than `min_age`; SKIP LOCKED keeps
        concurrent sweeps disjoint.

        A younger row may still be in its after-commit publish, which kiqs and
        marks the row published in two steps: taking it here would publish it a
        second time. Age is measured on the database clock, the same one that
        stamped `created_at`, so app-server skew cannot shrink the window.
        """
        query = (
            select(self.model)
            .where(
                self.model.status == OutboxMessageStatus.PENDING,
                self.model.created_at < func.now() - min_age,
            )
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
        self,
        session: AsyncSession,
        message_id: UUID,
        error: str,
        final: bool,
        *,
        count_attempt: bool = True,
    ) -> None:
        values: dict[str, Any] = {"last_error": error[:LAST_ERROR_MAX_LENGTH]}
        if count_attempt:
            values["attempts"] = self.model.attempts + 1
        if final:
            values["status"] = OutboxMessageStatus.FAILED
        await session.execute(
            update(self.model).where(self.model.id == message_id).values(**values)
        )

    async def purge(
        self,
        session: AsyncSession,
        status: Literal[OutboxMessageStatus.PUBLISHED, OutboxMessageStatus.FAILED],
        cutoff: datetime,
        *,
        limit: int,
    ) -> int:
        """
        Delete at most `limit` rows that reached `status` before `cutoff`, skipping
        rows another transaction holds.
        """
        # A row has no failure time of its own: `updated_at` is when it failed,
        # because nothing writes a FAILED row again.
        reached_at = {
            OutboxMessageStatus.PUBLISHED: self.model.published_at,
            OutboxMessageStatus.FAILED: self.model.updated_at,
        }[status]
        # MATERIALIZED: inlined, the planner may run the LIMIT subquery more than
        # once and delete more than `limit` rows.
        batch = (
            select(self.model.id)
            .where(self.model.status == status, reached_at < cutoff)
            .order_by(reached_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("batch")
            .prefix_with("MATERIALIZED", dialect="postgresql")
        )
        result = await session.execute(
            delete(self.model)
            .where(self.model.id.in_(select(batch.c.id)))
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount) if hasattr(result, "rowcount") else 0
