from typing import Annotated

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceProcessingException
from src.core.schemas import SuccessResponse
from src.core.utils.security import build_throttle_key, mask_email
from src.user.auth.schemas import ResendVerificationModel
from src.user.auth.services.email_notifier import (
    EmailNotifier,
    get_verification_notifier,
)
from src.user.policies import verification_pending

logger = get_logger(__name__)


class SendVerificationUseCase:
    """
    Resend the verification email.

    Answers success whether or not anything was sent: an unknown address and an
    already verified one must look identical from outside, or the endpoint
    confirms who has an account. The notifier's throttle key is released when
    the transaction fails to commit, so a failed attempt does not lock the
    address out of a retry.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        notifier: EmailNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(self, data: ResendVerificationModel) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(session=uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[ResendVerification] User with email '%s' not found.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)
            if not verification_pending(user):
                logger.debug(
                    "[ResendVerification] User with email '%s' already verified.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            throttle_key = build_throttle_key("resend_verification", user.email)
            try:
                await self.notifier.send(
                    uow=uow,
                    user=user,
                    throttle_key=throttle_key,
                )
            except InstanceProcessingException:
                logger.debug(
                    "[ResendVerification] Skip sending to email '%s' due to throttle",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            try:
                await uow.commit()
            except Exception:
                # The rollback discards the outbox row, but the throttle key in
                # Redis survives it: release it so a retry is not locked out
                # for the full TTL with no email queued.
                await self.notifier.release_throttle(throttle_key)
                raise
            return SuccessResponse(success=True)


def get_send_verification_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    notifier: Annotated[EmailNotifier, Depends(get_verification_notifier)],
) -> SendVerificationUseCase:
    return SendVerificationUseCase(uow=uow, notifier=notifier)
