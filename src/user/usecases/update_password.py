from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import (
    InstanceNotFoundException,
    InstanceProcessingException,
)
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import hash_password, mask_email, verify_password
from src.user.auth.errors import InvalidCredentialsError
from src.user.auth.schemas import UserNewPassword
from src.user.auth.token_helpers import invalidate_all_user_sessions
from src.user.cache_keys import user_cache_keys

logger = get_logger(__name__)


class UpdateUserPasswordUseCase:
    """
    Update a user's password and invalidate all their active sessions.

    This is the self-service change and it always proves knowledge of the
    current password. An administrative reset of someone else's password, when
    one is added, is a separate scenario and must not ask for it.

    Inputs:
    - data: UserNewPassword containing the current and the new password.
    - user_id: UUID of the user updating their password.

    Validations:
    - User must exist in the database.
    - current_password must match the stored hash.
    - The new password must differ from the current one.

    Workflow:
    1) Load the user and verify the current password.
    2) Hash and update user password in the database.
    3) Flush pending DB changes.
    4) Invalidate all active Redis sessions for the user.
    5) Invalidate the user cache namespace.
    6) Commit the transaction.

    Side effects:
    - Updates user record in database.
    - Deletes all user session keys from Redis before commit to avoid
      partial-success password changes when Redis is unavailable.
    - Bumps the user:{id} cache namespace version.

    Errors:
    - InstanceNotFoundException: if the user does not exist.
    - InvalidCredentialsError: if current_password does not match.
    - InstanceProcessingException: if the new password repeats the current one.

    Returns:
    - SuccessResponse: success=True.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        redis_client: Redis,
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client
        self.cache = cache

    async def execute(self, data: UserNewPassword, user_id: UUID) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, id=user_id)
            if not user:
                logger.info("[UpdateUserPassword] User not found.")
                raise InstanceNotFoundException("User not found.")

            if not await verify_password(data.current_password, user.password_hash):
                logger.debug(
                    "[UpdateUserPassword] Wrong current password for %s.",
                    mask_email(user.email),
                )
                raise InvalidCredentialsError("Current password is incorrect.")

            if data.password == data.current_password:
                # Not a no-op: going through with it would still sign every
                # session out, so a mistyped form would look like a hijack.
                raise InstanceProcessingException(
                    "New password must differ from the current one."
                )

            new_password_hash = await hash_password(data.password)
            update_data = {"password_hash": new_password_hash}
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                raise InstanceNotFoundException("User not found.")
            await uow.flush()
            await invalidate_all_user_sessions(str(updated_user.id), self.redis_client)
            await self.cache.invalidate(user_cache_keys.namespace(updated_user.id))
            uow.add_after_commit_hook(
                partial(
                    self.cache.invalidate, user_cache_keys.namespace(updated_user.id)
                )
            )
            await uow.commit()
            logger.debug(
                "[UpdateUserPassword] %s password updated successfully.",
                mask_email(updated_user.email),
            )
            logger.debug(
                "[UpdateUserPassword] All user %s sessions invalidated.",
                mask_email(updated_user.email),
            )
            return SuccessResponse(success=True)


def get_update_user_password_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> UpdateUserPasswordUseCase:
    return UpdateUserPasswordUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
