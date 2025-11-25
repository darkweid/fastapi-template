import asyncio
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from celery_tasks.main import (
    celery_app,  # noqa: F401
    local_async_session,
)
from celery_tasks.types import typed_shared_task
from loggers import get_logger
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)


@typed_shared_task(name="cleanup_unverified_users")
def cleanup_unverified_users() -> int:
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(_soft_delete_unverified_users())
    return result


async def _soft_delete_unverified_users() -> int:
    from src.user.models import User

    cutoff = get_utc_now() - timedelta(days=3)

    async with local_async_session() as session:
        try:
            stmt = (
                update(User)
                .where(
                    User.is_deleted.is_(False),
                    User.is_verified.is_(False),
                    User.created_at < cutoff,
                )
                .values(is_deleted=True, deleted_at=get_utc_now())
            )
            result = await session.execute(stmt)
            await session.commit()
            deleted_count = result.rowcount if hasattr(result, "rowcount") else 0
            return deleted_count
        except (IntegrityError, SQLAlchemyError) as e:
            await session.rollback()
            logger.exception("Batch soft-delete failed: %s", e)
            return 0
