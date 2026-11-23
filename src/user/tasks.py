from datetime import datetime, timedelta
from functools import partial
from typing import Annotated
from uuid import UUID

from redis.asyncio import Redis
import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession
from taskiq import TaskiqDepends

from loggers import get_logger
from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.scrubbed import scrubbed
from src.core.utils.datetime_utils import get_utc_now
from src.event_log.actor import Actor
from src.user.auth.realm import (
    RESET_PASSWORD_PURPOSE,
    USER_AUTH_REALM,
    VERIFICATION_PURPOSE,
)
from src.user.cache_keys import user_cache_keys
from src.user.events import UnverifiedUserDeleted
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client, get_tasks_session

logger = get_logger(__name__)

UNVERIFIED_USER_MAX_AGE = timedelta(days=3)
# Each deleted user costs a journal SAVEPOINT and two Redis calls inside the
# transaction that holds the batch's row locks.
CLEANUP_BATCH_SIZE = 100


@broker.task(
    task_name="cleanup_unverified_users",
    schedule=[{"cron": "0 */10 * * *"}],
)
async def cleanup_unverified_users(
    *,
    session: Annotated[AsyncSession, TaskiqDepends(get_tasks_session)],
    redis_client: Annotated[Redis, TaskiqDepends(get_tasks_redis_client)],
    cache: Annotated[Cache, TaskiqDepends(get_cache)],
) -> str:
    """
    Soft-delete users that never verified their account within the max age.

    Works in batches, one transaction each, in ascending id order. A batch that
    fails is reported and passed over: the next run takes its users again. A
    batch whose ids could not even be read ends the run.

    Side effects:
    - Retires the account's verification and reset challenges before the
      commit: they are keyed by email, so a link that outlived the account
      would act on whoever registers that address next.
    - Appends `user.unverified_deleted` per user, by the system actor.
    - Bumps the user:{id} cache namespace of every deleted user after the
      commit.
    """
    created_before = get_utc_now() - UNVERIFIED_USER_MAX_AGE
    deleted_count = 0
    last_id: UUID | None = None
    while True:
        batch: list[UUID] = []
        try:
            uow = ApplicationUnitOfWork(session)
            async with uow:
                batch = await uow.users.lock_unverified_ids(
                    uow.session,
                    created_before=created_before,
                    after_id=last_id,
                    limit=CLEANUP_BATCH_SIZE,
                )
                deleted_count += await _delete_batch(
                    uow, batch, created_before, redis_client, cache
                )
        except Exception as error:
            # Swallowed on purpose: periodic cleanup must not crash and retry
            # forever; a swallowed exception is the allowed manual-capture case.
            # A driver error quotes the row it failed on, emails included.
            failure = scrubbed(
                f"Unverified user cleanup batch failed: {type(error).__name__}", error
            )
            logger.error("Unverified user cleanup batch failed", exc_info=failure)
            sentry_sdk.capture_exception(failure)
        if not batch:
            return f"Deleted {deleted_count} unverified users."
        last_id = batch[-1]


async def _delete_batch(
    uow: ApplicationUnitOfWork,
    ids: list[UUID],
    created_before: datetime,
    redis_client: Redis,
    cache: Cache,
) -> int:
    """Commits. Returns how many of `ids` were deleted."""
    if not ids:
        return 0
    deleted = await uow.users.soft_delete_unverified(
        uow.session, ids, created_before=created_before
    )
    challenges = ActiveChallengeRegistry(USER_AUTH_REALM)
    for user in deleted:
        for purpose in (VERIFICATION_PURPOSE, RESET_PASSWORD_PURPOSE):
            await challenges.invalidate(purpose, user.email, redis_client)
        uow.add_after_commit_hook(
            partial(cache.invalidate, user_cache_keys.namespace(user.id))
        )
        await uow.publish(Actor.system(), UnverifiedUserDeleted(object_id=user.id))
    await uow.commit()
    return len(deleted)
