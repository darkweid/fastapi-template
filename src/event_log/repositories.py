from typing import Any

import sentry_sdk
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger
from src.core.database.repositories import BaseRepository
from src.event_log.actor import Actor
from src.event_log.events import DomainEvent
from src.event_log.models import EventLog

logger = get_logger(__name__)

# A column of its own; repeating it inside the payload would let the two copies
# drift apart.
_PAYLOAD_EXCLUDED_FIELDS = {"object_id"}


class EventLogRepository(BaseRepository[EventLog]):
    model = EventLog
    # Nothing is searchable: `payload` is JSONB and the remaining columns are
    # identifiers a caller filters by exactly, not with a substring.
    sortable_fields = ("created_at",)
    # A field name, never a direction: `_assert_list_query_fields` resolves it
    # with `getattr(self.model, ...)` at construction time, and `ListQuery`
    # supplies the descending order.
    default_order_by = "created_at"

    async def record(
        self, session: AsyncSession, actor: Actor, event: DomainEvent
    ) -> None:
        """Append one row, and never let that failure become the caller's.

        Call inside the use case's UoW, before its `commit()`: the row is part
        of the same transaction as the action it describes, so a rolled-back
        action leaves no audit trail claiming it happened.

        The caller's pending changes are flushed first, outside the SAVEPOINT:
        `begin_nested()` flushes the session itself, so without this line a
        unique or NOT NULL violation of the *action* would surface from inside
        the block below, be swallowed as a logging failure, and leave the
        caller with a 500 and a false Sentry issue instead of their own error.
        """
        await session.flush()
        payload: dict[str, Any] = event.model_dump(
            mode="json", exclude=_PAYLOAD_EXCLUDED_FIELDS
        )
        try:
            async with session.begin_nested():
                session.add(
                    EventLog(
                        actor_type=actor.actor_type,
                        actor_id=actor.actor_id,
                        object_type=event.object_type,
                        object_id=event.object_id,
                        event_type=event.code,
                        payload=payload,
                        ip_address=actor.ip,
                    )
                )
        except SQLAlchemyError as error:
            # Swallowed on purpose: an audit row is never worth failing the
            # action it describes. The SAVEPOINT is what keeps that possible -
            # its rollback undoes the failed INSERT and leaves the caller's own
            # work intact. Sentry is what keeps the failure visible.
            logger.error("[EventLog] Failed to record %s", event.code, exc_info=error)
            sentry_sdk.capture_exception(error)
