from typing import Annotated

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceProcessingException
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email
from src.event_log.actor import Actor
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import SendResetPasswordRequestModel
from src.user.auth.services.email_notifier import (
    EmailNotifier,
    get_reset_password_notifier,
)
from src.user.events import UserPasswordResetRequested

logger = get_logger(__name__)


class ResetPasswordRequestUseCase:
    """
    Send a password reset email.

    Answers success whether or not anything was sent, the unelapsed throttle
    window included: a caller must not be able to tell an unknown address from
    one that was mailed a minute ago. The throttle key is released when the
    transaction fails to commit.

    Side effects:
    - Appends `user.password_reset_requested` to the event log when an email
      is actually queued.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        notifier: EmailNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(
        self, data: SendResetPasswordRequestModel, ip: str | None = None
    ) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[ResetPasswordRequest] User with email %s not found.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            throttle_key = USER_AUTH_REALM.keys.throttle("password-reset", user.email)
            try:
                await self.notifier.send(
                    uow=uow,
                    user=user,
                    throttle_key=throttle_key,
                )
            except InstanceProcessingException:
                logger.debug(
                    "[ResetPasswordRequest] Skip sending to email '%s' due to throttle",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            # Anonymous: anyone may type an address into that form, and this
            # row is what later tells a takeover attempt from a forgotten
            # password. The throttled and unknown-address branches above
            # return before it - neither queued an email.
            await uow.event_logs.record(
                uow.session,
                Actor.anonymous(ip=ip),
                UserPasswordResetRequested(object_id=user.id),
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
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    notifier: Annotated[EmailNotifier, Depends(get_reset_password_notifier)],
) -> ResetPasswordRequestUseCase:
    return ResetPasswordRequestUseCase(uow=uow, notifier=notifier)
