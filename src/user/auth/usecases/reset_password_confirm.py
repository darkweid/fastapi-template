from fastapi import Depends
import jwt

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email
from src.main.config import config
from src.user.auth.schemas import ResetPasswordModel
from src.user.auth.token_helpers import invalidate_all_user_sessions

logger = get_logger(__name__)


class ResetPasswordConfirmUseCase:
    """Use case for resetting password with a reset token."""

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
    ) -> None:
        self.uow = uow

    async def execute(
        self,
        data: ResetPasswordModel,
    ) -> SuccessResponse:
        async with self.uow as uow:
            try:
                payload = jwt.decode(
                    data.token,
                    config.jwt.JWT_RESET_PASSWORD_SECRET_KEY,
                    [config.jwt.ALGORITHM],
                )

                if payload.get("mode") == "reset_password_token":
                    email = payload.get("email")
                    if not email:
                        logger.info("[ResetPasswordConfirm] Email not found in token.")
                        return SuccessResponse(success=False)

                    user = await uow.users.update(
                        uow.session,
                        {"password": data.password},
                        email=email,
                    )
                    await uow.session.flush()
                    if not user:
                        logger.info(
                            "[ResetPasswordConfirm] User with email %s not found.",
                            mask_email(email),
                        )
                        return SuccessResponse(success=False)

                    await invalidate_all_user_sessions(str(user.id))
                    logger.debug(
                        "[ResetPasswordConfirm] All user %s sessions invalidated.",
                        mask_email(email),
                    )
                    await uow.commit()
                    logger.info(
                        "[ResetPasswordConfirm] Successfully changed password for user with email %s.",
                        mask_email(email),
                    )
                    return SuccessResponse(success=True)
                else:
                    logger.info("[ResetPasswordConfirm] Invalid token mode.")
                    return SuccessResponse(success=False)

            except jwt.ExpiredSignatureError:
                logger.info("[ResetPasswordConfirm] Token has expired.")
                return SuccessResponse(success=False)

            except jwt.InvalidTokenError:
                logger.info("[ResetPasswordConfirm] Token is invalid. Ema")
                return SuccessResponse(success=False)


def get_reset_password_confirm_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
) -> ResetPasswordConfirmUseCase:
    return ResetPasswordConfirmUseCase(
        uow=uow,
    )
