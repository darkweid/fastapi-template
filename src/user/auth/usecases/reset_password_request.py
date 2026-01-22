from fastapi import Depends
from starlette.datastructures import URL

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.schemas import SuccessResponse
from src.core.utils.security import build_email_throttle_key, mask_email
from src.user.auth.schemas import SendResetPasswordRequestModel
from src.user.auth.services.reset_password_notifier import (
    ResetPasswordNotifier,
    get_reset_password_notifier,
)

logger = get_logger(__name__)


class ResetPasswordRequestUseCase:
    """
    Request a password reset email for a user.

    Inputs:
    - data: SendResetPasswordRequestModel containing user email.
    - request_base_url: The base URL for the password reset link.

    Validations:
    - User must exist (if not, return success to prevent email enumeration).

    Workflow:
    1) Retrieve user by email.
    2) If user exists, send password reset email using the notifier with throttling.

    Side effects:
    - Sends an external email notification.
    - Sets/updates a throttle key in Redis.

    Errors:
    - InfrastructureException: if email sending fails.

    Returns:
    - SuccessResponse: success=True regardless of whether email was sent.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        notifier: ResetPasswordNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(
        self, data: SendResetPasswordRequestModel, request_base_url: URL
    ) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[ResetPasswordRequest] User with email %s not found.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            throttle_key = build_email_throttle_key("password-reset", user.email)
            await self.notifier.send_password_reset_email(
                user=user,
                base_url=request_base_url,
                throttle_key=throttle_key,
            )

            logger.info(
                "[ResetPasswordRequest] Reset password email successfully sent to %s",
                mask_email(data.email),
            )
            return SuccessResponse(success=True)


def get_reset_password_request_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    notifier: ResetPasswordNotifier = Depends(get_reset_password_notifier),
) -> ResetPasswordRequestUseCase:
    return ResetPasswordRequestUseCase(uow=uow, notifier=notifier)
