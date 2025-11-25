from uuid import UUID

from fastapi import Depends

from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email
from loggers import get_logger
from src.user.auth.schemas import UserNewPassword
from src.user.auth.token_helpers import invalidate_all_user_sessions

logger = get_logger(__name__)


class UpdateUserPasswordUseCase:
    """Use case for updating password."""

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
    ) -> None:
        self.uow = uow

    async def execute(self, data: UserNewPassword, user_id: UUID) -> SuccessResponse:
        async with self.uow as uow:
            update_data = {"password": data.password}
            updated_user = await uow.users.update(uow.session, update_data, id=user_id)
            if not updated_user:
                logger.info("[UpdateUserPassword] User not found.")
                return SuccessResponse(success=False)
            await uow.session.flush()
            logger.debug(
                "[UpdateUserPassword] %s password updated successfully.",
                mask_email(updated_user.email),
            )

            await invalidate_all_user_sessions(str(updated_user.id))
            logger.debug(
                "[UpdateUserPassword] All user %s sessions invalidated.",
                mask_email(updated_user.email),
            )

            await uow.commit()
            return SuccessResponse(success=True)


def get_update_user_password_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
) -> UpdateUserPasswordUseCase:
    return UpdateUserPasswordUseCase(
        uow=uow,
    )
