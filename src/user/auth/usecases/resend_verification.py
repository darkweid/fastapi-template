from fastapi import Depends
from httpx import URL
from redis.asyncio import Redis

from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.email_service.dependencies import get_email_service
from src.core.email_service.service import EmailService
from src.core.errors.exceptions import InstanceProcessingException
from src.core.redis.client import redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email, build_email_throttle_key
from src.user.auth.services.verification_notifier import VerificationNotifier
from src.user.auth.schemas import ResendVerificationModel
from loggers import get_logger

logger = get_logger(__name__)


class SendVerificationUseCase:
    """Use case for sending verification email."""

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        email_service: EmailService,
        redis_client: Redis,
    ) -> None:
        self.uow = uow
        self.email_service = email_service
        self.redis_client = redis_client

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

            notifier = VerificationNotifier(
                email_service=self.email_service, redis_client=self.redis_client
            )
            throttle_key = build_email_throttle_key("resend_verification", user.email)
            try:
                await notifier.send_verification(
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
    email_service: EmailService = Depends(get_email_service),
) -> SendVerificationUseCase:
    return SendVerificationUseCase(
        uow=uow, email_service=email_service, redis_client=redis_client
    )
