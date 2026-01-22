from fastapi import Depends
from starlette.datastructures import URL

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import InstanceProcessingException
from src.core.schemas import SuccessResponse
from src.core.utils.security import build_email_throttle_key, mask_email
from src.user.auth.schemas import ResendVerificationModel
from src.user.auth.services.verification_notifier import (
    VerificationNotifier,
    get_verification_notifier,
)

logger = get_logger(__name__)


class SendVerificationUseCase:
    """
    Resend a verification email to a user.

    Inputs:
    - data: ResendVerificationModel containing user email.
    - request_base_url: The base URL for the verification link.

    Validations:
    - User must exist (if not, return success to prevent email enumeration).
    - User must not be already verified (if so, return success).

    Workflow:
    1) Retrieve user by email.
    2) Check if user is already verified.
    3) Send verification email using the notifier with throttling.

    Side effects:
    - Sends an external email notification.
    - Sets/updates a throttle key in Redis.

    Errors:
    - InfrastructureException: if email sending fails.

    Returns:
    - SuccessResponse: success=True regardless of whether email was sent (for privacy).
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        notifier: VerificationNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(
        self, data: ResendVerificationModel, request_base_url: URL
    ) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(session=uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[ResendVerification] User with email '%s' not found.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)
            if user.is_verified:
                logger.debug(
                    "[ResendVerification] User with email '%s' already verified.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            throttle_key = build_email_throttle_key("resend_verification", user.email)
            try:
                await self.notifier.send_verification(
                    user=user,
                    base_url=request_base_url,
                    throttle_key=throttle_key,
                )
            except InstanceProcessingException:
                logger.debug(
                    "[ResendVerification] Skip sending to email '%s' due to throttle",
                    mask_email(data.email),
                )

            return SuccessResponse(success=True)


def get_send_verification_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    notifier: VerificationNotifier = Depends(get_verification_notifier),
) -> SendVerificationUseCase:
    return SendVerificationUseCase(uow=uow, notifier=notifier)
