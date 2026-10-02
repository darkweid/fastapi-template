import sentry_sdk
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger
from src.core.database.repositories import BaseRepository
from src.event_log.events import PublishedEvent
from src.event_log.models import EventLog

logger = get_logger(__name__)


class EventLogRepository(BaseRepository[EventLog]):
    model = EventLog
    # Nothing is searchable: `payload` is JSONB and the remaining columns are
    # identifiers a caller filters by exactly, not with a substring.
    sortable_fields = ("created_at",)
    # A field name, never a direction: `_assert_list_query_fields` resolves it
    # with `getattr(self.model, ...)` at construction time, and `ListQuery`
    # supplies the descending order.
    default_order_by = "created_at"

    async def record(self, session: AsyncSession, published: PublishedEvent) -> None:
        """Append one row, and never let that failure become the caller's.

        Called by `ApplicationUnitOfWork.publish`, never by a use case: the
        journal is one reader of an event, and nothing else may hang on
        whether its row was written. The row is part of the publisher's
        transaction, so a rolled-back action leaves no audit trail claiming it
        happened.

        The caller's pending changes are flushed first, outside the SAVEPOINT:
        `begin_nested()` flushes the session itself, so without this line a
        unique or NOT NULL violation of the *action* would surface from inside
        the block below, be swallowed as a logging failure, and leave the
        caller with a 500 and a false Sentry issue instead of their own error.
        """
        await session.flush()
        event = published.event
        try:
            async with session.begin_nested():
                session.add(
                    EventLog(
                        id=published.id,
                        actor_type=published.actor.actor_type,
                        actor_id=published.actor.actor_id,
                        object_type=event.object_type,
                        object_id=event.object_id,
                        event_type=event.code,
                        payload=published.payload(),
                        ip_address=published.actor.ip,
                        created_at=published.occurred_at,
                    )
                )
        except SQLAlchemyError as error:
            # Swallowed on purpose: an audit row is never worth failing the
            # action it describes. The SAVEPOINT is what keeps that possible -
            # its rollback undoes the failed INSERT and leaves the caller's own
            # work intact. Sentry is what keeps the failure visible.
            logger.error("[EventLog] Failed to record %s", event.code, exc_info=error)
            sentry_sdk.capture_exception(error)
