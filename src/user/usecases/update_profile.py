from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import InstanceNotFoundException
from src.user.cache_keys import user_cache_keys
from src.user.schemas import UserProfileUpdateModel, UserProfileViewModel

logger = get_logger(__name__)


class UpdateUserProfileUseCase:
    """
    Update the current user's profile fields.

    Inputs:
    - data: UserProfileUpdateModel with the fields to change. A field left unset
      is skipped rather than cleared; an explicit `null` is likewise skipped, not
      rejected, since the schema has no way to tell "omitted" from "set to null"
      apart from Pydantic's own unset-tracking, which this UseCase does not use.
      An entirely empty body is a no-op write: it still updates zero columns,
      still flushes, and still bumps the cache namespace. Both are the safe
      direction - a spurious bump costs a cold cache, not a stale read.
    - user_id: UUID of the user being updated.

    Validations:
    - User must exist.

    Workflow:
    1) Apply the non-empty fields to the user row.
    2) Flush pending DB changes.
    3) Invalidate the user cache namespace.
    4) Commit the transaction.

    Side effects:
    - Updates the user record.
    - Bumps the user:{id} cache namespace version.

    Errors:
    - InstanceNotFoundException: if the user does not exist.

    Returns:
    - UserProfileViewModel: the updated profile.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.cache = cache

    async def execute(
        self, data: UserProfileUpdateModel, user_id: UUID
    ) -> UserProfileViewModel:
        update_data = data.model_dump(exclude_none=True)
        async with self.uow as uow:
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                raise InstanceNotFoundException("User not found.")
            await uow.flush()
            # Invalidation happens before commit on purpose: a bump that outlives a
            # rolled-back transaction only costs a cold cache, while a bump skipped
            # after a successful commit would serve stale data.
            await self.cache.invalidate(user_cache_keys.namespace(user_id))
            await uow.commit()
            logger.debug("[UpdateUserProfile] user %s profile updated.", user_id)
            return UserProfileViewModel.model_validate(updated_user)


def get_update_user_profile_use_case(
    uow: Annotated[
        ApplicationUnitOfWork[RepositoryProtocol], Depends(get_unit_of_work)
    ],
    cache: Annotated[Cache, Depends(get_cache)],
) -> UpdateUserProfileUseCase:
    return UpdateUserProfileUseCase(uow=uow, cache=cache)
