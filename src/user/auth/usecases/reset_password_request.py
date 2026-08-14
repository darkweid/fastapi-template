from typing import Annotated

from fastapi import Depends

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

    Validations:
    - User must exist (if not, return success to prevent email enumeration).

    Workflow:
    1) Retrieve user by email.
    2) If user exists, store the password reset email delivery in the outbox
       using the notifier with throttling.
    3) Commit the transaction (the outbox publish hook fires after commit).

    Side effects:
    - Inserts an outbox row for the password reset email and publishes it
      after commit.
    - Sets/updates a throttle key in Redis; releases it if the transaction
      fails to commit.

    Errors:
    - InstanceProcessingException: if a reset email was already sent recently
      and the throttle window has not elapsed.

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

    async def execute(self, data: SendResetPasswordRequestModel) -> SuccessResponse:
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
                uow=uow,
                user=user,
                throttle_key=throttle_key,
            )
            try:
                await uow.commit()
            except Exception:
                # The rollback discards the outbox row, but the throttle key in
                # Redis survives it: release it so a retry is not locked out
                # for the full TTL with no email queued.
                await self.notifier.release_throttle(throttle_key)
                raise

            logger.info(
                "[ResetPasswordRequest] Reset password email successfully sent to %s",
                mask_email(data.email),
            )
            return SuccessResponse(success=True)


def get_reset_password_request_use_case(
    uow: Annotated[
        ApplicationUnitOfWork[RepositoryProtocol], Depends(get_unit_of_work)
    ],
    notifier: Annotated[ResetPasswordNotifier, Depends(get_reset_password_notifier)],
) -> ResetPasswordRequestUseCase:
    return ResetPasswordRequestUseCase(uow=uow, notifier=notifier)
