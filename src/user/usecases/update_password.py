from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import hash_password, mask_email
from src.user.auth.schemas import UserNewPassword
from src.user.auth.token_helpers import invalidate_all_user_sessions

logger = get_logger(__name__)


class UpdateUserPasswordUseCase:
    """
    Update a user's password and invalidate all their active sessions.

    Inputs:
    - data: UserNewPassword containing the new password.
    - user_id: UUID of the user updating their password.

    Validations:
    - User must exist in the database.

    Workflow:
    1) Hash and update user password in the database.
    2) Commit the transaction.
    3) Log success and invalidate all active Redis sessions for the user.

    Side effects:
    - Updates user record in database.
    - Deletes all user session keys from Redis after a successful commit.

    Errors:
    - InstanceProcessingException: if update fails.

    Returns:
    - SuccessResponse: success=True if updated, False if user not found.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        redis_client: Redis,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client

    async def execute(self, data: UserNewPassword, user_id: UUID) -> SuccessResponse:
        async with self.uow as uow:
            update_data = {"password_hash": hash_password(data.password)}
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                logger.info("[UpdateUserPassword] User not found.")
                return SuccessResponse(success=False)
            await uow.commit()
            logger.debug(
                "[UpdateUserPassword] %s password updated successfully.",
                mask_email(updated_user.email),
            )

            await invalidate_all_user_sessions(str(updated_user.id), self.redis_client)
            logger.debug(
                "[UpdateUserPassword] All user %s sessions invalidated.",
                mask_email(updated_user.email),
            )
            return SuccessResponse(success=True)


def get_update_user_password_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    redis_client: Redis = Depends(get_redis_client),
) -> UpdateUserPasswordUseCase:
    return UpdateUserPasswordUseCase(
        uow=uow,
        redis_client=redis_client,
    )
