from datetime import timedelta
from typing import Annotated

import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.database.filters import FilterCondition
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.utils.datetime_utils import get_utc_now
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_session

logger = get_logger(__name__)

UNVERIFIED_USER_MAX_AGE = timedelta(days=3)


@broker.task(
    task_name="cleanup_unverified_users",
    schedule=[{"cron": "0 */10 * * *"}],
)
async def cleanup_unverified_users(
    *,
    session: Annotated[AsyncSession, TaskiqDepends(get_tasks_session)],
) -> str:
    """Soft-delete users that never verified their account within the max age."""
    cutoff = get_utc_now() - UNVERIFIED_USER_MAX_AGE
    uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
    try:
        async with uow:
            deleted_count = await uow.users.batch_soft_delete(
                session=uow.session,
                filters=FilterCondition(
                    eq={"is_verified": False},
                    lt={"created_at": cutoff},
                ),
            )
            await uow.commit()
    except Exception as e:
        # Swallowed on purpose: periodic cleanup must not crash and retry
        # forever; a swallowed exception is the allowed manual-capture case.
        logger.exception("Batch soft-delete failed: %s", e)
        sentry_sdk.capture_exception(e)
        return "Deleted 0 unverified users."
    return f"Deleted {int(deleted_count)} unverified users."
