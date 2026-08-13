from datetime import timedelta
from typing import Annotated

import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.outbox.models import OutboxMessage
from src.core.utils.datetime_utils import get_utc_now
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_session

logger = get_logger(__name__)

SWEEPER_BATCH_SIZE = 100
MAX_PUBLISH_ATTEMPTS = 10
PUBLISHED_RETENTION_DAYS = 7


def _report_failed_message(message: OutboxMessage) -> None:
    # Degraded-infrastructure signal: a row exhausted its publish attempts (or
    # references an unregistered task) and needs manual attention.
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("outbox_message_id", str(message.id))
        scope.set_tag("task_name", message.task_name)
        sentry_sdk.capture_message("Outbox message moved to FAILED", level="error")


@broker.task(task_name="outbox_sweeper", schedule=[{"cron": "* * * * *"}])
async def outbox_sweeper(
    *,
    session: Annotated[AsyncSession, TaskiqDepends(get_tasks_session)],
) -> str:
    """Publish pending outbox rows whose after-commit publish did not happen."""
    published = 0
    failed = 0
    uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
    async with uow:
        batch = await uow.outbox.get_batch_for_publish(
            uow.session, limit=SWEEPER_BATCH_SIZE
        )
        for message in batch:
            task = broker.find_task(message.task_name)
            if task is None:
                await uow.outbox.mark_publish_failure(
                    uow.session,
                    message.id,
                    "task is not registered in the broker",
                    final=True,
                )
                _report_failed_message(message)
                failed += 1
                continue
            try:
                await task.kicker().with_task_id(str(message.id)).kiq(
                    *message.args, **message.kwargs
                )
            except Exception as exc:
                final = message.attempts + 1 >= MAX_PUBLISH_ATTEMPTS
                await uow.outbox.mark_publish_failure(
                    uow.session, message.id, str(exc), final=final
                )
                if final:
                    _report_failed_message(message)
                failed += 1
                continue
            await uow.outbox.mark_published(uow.session, message.id)
            published += 1
        await uow.commit()
    if published or failed:
        logger.info("Outbox sweep: published %d, failed %d", published, failed)
    return f"Published {published}, failed {failed}."


@broker.task(task_name="outbox_purge", schedule=[{"cron": "0 3 * * *"}])
async def outbox_purge(
    *,
    session: Annotated[AsyncSession, TaskiqDepends(get_tasks_session)],
) -> str:
    """Delete published outbox rows older than the retention window."""
    cutoff = get_utc_now() - timedelta(days=PUBLISHED_RETENTION_DAYS)
    uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
    async with uow:
        deleted = await uow.outbox.purge_published(uow.session, cutoff)
        await uow.commit()
    return f"Deleted {deleted} published outbox messages."
