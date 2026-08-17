from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.user.cache_keys import user_cache_keys
from src.user.schemas import UserProfileUpdateModel, UserProfileViewModel

logger = get_logger(__name__)


class UpdateUserProfileUseCase:
    """
    Update the current user's profile fields.

    Inputs:
    - data: UserProfileUpdateModel with the fields to change. A field left unset
      is skipped (exclude_unset); an explicit `null` fails schema validation,
      because every updatable column here is non-nullable. An entirely empty
      body is a no-op write: it still updates zero columns, still flushes, and
      still bumps the cache namespace - a spurious bump costs a cold cache, not
      a stale read.
    - user_id: UUID of the user being updated.

    Validations:
    - User must exist.

    Workflow:
    1) Apply the non-empty fields to the user row.
    2) Flush pending DB changes.
    3) Invalidate the user cache namespace.
    4) Commit the transaction; an after-commit hook invalidates the
       namespace a second time.

    Side effects:
    - Updates the user record.
    - Bumps the user:{id} cache namespace version twice (pre- and post-commit).

    Errors:
    - InstanceNotFoundException: if the user does not exist.

    Returns:
    - UserProfileViewModel: the updated profile.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.cache = cache

    async def execute(
        self, data: UserProfileUpdateModel, user_id: UUID
    ) -> UserProfileViewModel:
        update_data = data.model_dump(exclude_unset=True)
        async with self.uow as uow:
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                raise InstanceNotFoundException("User not found.")
            await uow.flush()
            # Two bumps by design. Pre-commit covers the crash direction: a bump
            # that outlives a rolled-back transaction only costs a cold cache.
            # The post-commit bump closes the other race: a reader who re-cached
            # the old row between the first bump and the commit would otherwise
            # serve stale data until the TTL.
            await self.cache.invalidate(user_cache_keys.namespace(user_id))
            uow.add_after_commit_hook(
                partial(self.cache.invalidate, user_cache_keys.namespace(user_id))
            )
            await uow.commit()
            logger.debug("[UpdateUserProfile] user %s profile updated.", user_id)
            return UserProfileViewModel.model_validate(updated_user)


def get_update_user_profile_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> UpdateUserProfileUseCase:
    return UpdateUserProfileUseCase(uow=uow, cache=cache)
