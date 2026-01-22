from datetime import timedelta

import sentry_sdk
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from celery_tasks.main import (
    celery_app,  # noqa: F401
    local_async_session,
)
from celery_tasks.types import typed_shared_task
from loggers import get_logger
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.utils.coroutine_runner import execute_coroutine_sync
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)


@typed_shared_task(name="cleanup_unverified_users")
def cleanup_unverified_users() -> str:
    result = execute_coroutine_sync(coroutine=_soft_delete_unverified_users)
    return f"Deleted {result} unverified users."


async def _soft_delete_unverified_users() -> int:
    cutoff = get_utc_now() - timedelta(days=3)

    async with local_async_session() as session:
        uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
        try:
            async with uow:
                deleted_count = await uow.users.batch_soft_delete(
                    session=uow.session,
                    is_verified=False,
                    created_at_lt=cutoff,
                )
                await uow.commit()
                return int(deleted_count)
        except (IntegrityError, SQLAlchemyError, Exception) as e:
            logger.exception("Batch soft-delete failed: %s", e)
            sentry_sdk.capture_exception(e)
            return 0
