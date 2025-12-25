from fastapi import Depends
from redis.asyncio import Redis
from starlette.datastructures import URL

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.email_service.dependencies import get_email_service
from src.core.email_service.service import EmailService
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import build_email_throttle_key, mask_email
from src.user.auth.schemas import SendResetPasswordRequestModel
from src.user.auth.services.reset_password_notifier import ResetPasswordNotifier

logger = get_logger(__name__)


class ResetPasswordRequestUseCase:
    """Use case for requesting password reset."""

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
        self, data: SendResetPasswordRequestModel, request_base_url: URL
    ) -> SuccessResponse:
        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, email=data.email)
            if not user:
                logger.info(
                    "[ResetPasswordRequest] User with email %s not found.",
                    mask_email(data.email),
                )
                return SuccessResponse(success=True)

            notifier = ResetPasswordNotifier(
                email_service=self.email_service, redis_client=self.redis_client
            )
            throttle_key = build_email_throttle_key("password-reset", user.email)
            await notifier.send_password_reset_email(
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
    email_service: EmailService = Depends(get_email_service),
    redis_client: Redis = Depends(get_redis_client),
) -> ResetPasswordRequestUseCase:
    return ResetPasswordRequestUseCase(
        uow=uow, email_service=email_service, redis_client=redis_client
    )
