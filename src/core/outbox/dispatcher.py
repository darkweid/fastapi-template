from functools import partial
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from taskiq.decor import AsyncTaskiqDecoratedTask
import uuid6

from src.core.database.uow import ApplicationUnitOfWork
from src.core.outbox.repositories import OutboxRepository


class TaskDispatcher:
    """Single entry point for enqueueing background tasks.

    `enqueue` fires immediately; `enqueue_transactional` stores the task as an
    outbox row inside the caller's transaction and publishes it best-effort
    after commit (the sweeper guarantees delivery if that publish fails).
    Transactional arguments must be JSON-serializable - pass `str(uuid)`, ISO
    datetimes and plain dicts, not model instances.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._outbox_repository = OutboxRepository()

    async def enqueue(
        self, task: AsyncTaskiqDecoratedTask[Any, Any], *args: Any, **kwargs: Any
    ) -> None:
        await task.kiq(*args, **kwargs)

    async def enqueue_transactional(
        self,
        uow: ApplicationUnitOfWork,
        task: AsyncTaskiqDecoratedTask[Any, Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        # Arguments are stored in a JSONB outbox row; a non-JSON value (UUID,
        # datetime, NaN, model) would otherwise fail deep inside the INSERT
        # flush. The round-trip check additionally rejects values JSON merely
        # coerces (int dict keys, nested tuples): the inline publish would send
        # the original while a sweeper republish sends the coerced form - one
        # task, two different payloads.
        payload = {"args": list(args), "kwargs": kwargs}
        try:
            encoded_payload = json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"Arguments for task '{task.task_name}' must be JSON-serializable "
                f"(they are stored in a JSONB outbox row): {exc}"
            ) from exc
        if json.loads(encoded_payload) != payload:
            raise TypeError(
                f"Arguments for task '{task.task_name}' must survive a JSON "
                "round-trip unchanged (a sweeper republish sends the stored "
                "JSONB form): avoid int dict keys and nested tuples"
            )
        # Client-side id: the hook needs it before flush, and it doubles as the
        # broker task_id so the worker-side dedup marker survives republishing.
        # uuid7 to match the model's UUID7IDMixin default — pre-generating the
        # id here bypasses that default, so the generator must stay in sync.
        message_id = uuid6.uuid7()
        await uow.outbox.create(
            uow.session,
            {
                "id": message_id,
                "task_name": task.task_name,
                "args": list(args),
                "kwargs": kwargs,
            },
        )
        uow.add_after_commit_hook(
            partial(self._publish, task, message_id, args, kwargs)
        )

    async def _publish(
        self,
        task: AsyncTaskiqDecoratedTask[Any, Any],
        message_id: UUID,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        await task.kicker().with_task_id(str(message_id)).kiq(*args, **kwargs)
        async with self._session_factory() as session, session.begin():
            await self._outbox_repository.mark_published(session, message_id)
