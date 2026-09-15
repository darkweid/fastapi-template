from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.event_log.actor import Actor
from src.event_log.changes import changed_fields
from src.user.cache_keys import user_cache_keys
from src.user.events import UserProfileUpdated
from src.user.schemas import UserProfileUpdateModel, UserProfileViewModel

logger = get_logger(__name__)


class UpdateUserProfileUseCase:
    """
    Update the caller's profile fields.

    An unset field is skipped (exclude_unset) and an explicit null fails schema
    validation, because every updatable column here is non-nullable. An empty
    body is still a write: it updates zero columns, flushes, and bumps the
    cache namespace anyway - a spurious bump costs a cold cache, a skipped one
    could cost a stale read.

    Side effects:
    - Appends `user.profile_updated` to the event log when a column actually
      changed, carrying the field names.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.cache = cache

    async def execute(
        self, data: UserProfileUpdateModel, user_id: UUID, actor: Actor
    ) -> UserProfileViewModel:
        update_data = data.model_dump(exclude_unset=True)
        async with self.uow as uow:
            stored_user = await uow.users.get_single(uow.session, id=user_id)
            if not stored_user:
                raise InstanceNotFoundException("User not found.")
            # Read off the loaded row before `update()` overwrites it in place.
            changes = changed_fields(stored_user, update_data)
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                raise InstanceNotFoundException("User not found.")
            await uow.flush()
            # Two bumps by design. Pre-commit covers the crash direction: a bump
            # that outlives a rolled-back transaction only costs a cold cache.
            # The post-commit bump narrows the other race: a reader who re-cached
            # the old row between the first bump and the commit would otherwise
            # serve stale data until the TTL. A residual window remains - a
            # reader that read the old row before commit and writes its cache
            # entry after the second bump still lands stale data in the current
            # generation. Eliminating it needs version-conditional cache writes
            # (capture the generation at the miss, write only if unchanged);
            # accepted as bounded-by-TTL staleness instead of that complexity.
            await self.cache.invalidate(user_cache_keys.namespace(user_id))
            uow.add_after_commit_hook(
                partial(self.cache.invalidate, user_cache_keys.namespace(user_id))
            )
            if changes:
                # The empty body still bumps the cache above, but it changed no
                # column and has nothing to record.
                await uow.event_logs.record(
                    uow.session,
                    actor,
                    UserProfileUpdated(object_id=user_id, fields=sorted(changes)),
                )
            await uow.commit()
            logger.debug("[UpdateUserProfile] user %s profile updated.", user_id)
            return UserProfileViewModel.model_validate(updated_user)


def get_update_user_profile_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> UpdateUserProfileUseCase:
    return UpdateUserProfileUseCase(uow=uow, cache=cache)
