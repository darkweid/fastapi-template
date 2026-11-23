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
from src.user.policies import account_blocked

logger = get_logger(__name__)


class ResetPasswordRequestUseCase:
    """
    Send a password reset email.

    Answers success whether or not anything was sent, the unelapsed throttle
    window included: a caller must not be able to tell an unknown address from
    one that was mailed a minute ago. A blocked account is answered the same
    way and claims, sends and records nothing. The throttle key is released
    when anything after the claim fails, the commit included.

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

            if account_blocked(user):
                logger.debug(
                    "[ResetPasswordRequest] User with email %s is blocked.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            throttle_key = USER_AUTH_REALM.keys.throttle("password-reset", user.email)
            try:
                slot = await self.notifier.send(
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

            try:
                # Anonymous: anyone may type an address into that form, and this
                # row is what later tells a takeover attempt from a forgotten
                # password. The branches above return before it - none of them
                # queued an email.
                await uow.publish(
                    Actor.anonymous(ip=ip),
                    UserPasswordResetRequested(object_id=user.id),
                )
                await uow.commit()
            except Exception:
                # The rollback discards the outbox row, but the throttle key in
                # Redis survives it: release it so a retry is not locked out
                # for the full TTL with no email queued.
                if slot:
                    await self.notifier.release_throttle(slot)
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
