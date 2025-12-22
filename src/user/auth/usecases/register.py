from fastapi import Depends
from redis.asyncio import Redis
from starlette.datastructures import URL

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.email_service.dependencies import get_email_service
from src.core.email_service.service import EmailService
from src.core.redis.dependencies import get_redis_client
from src.core.utils.security import build_email_throttle_key
from src.user.auth.schemas import CreateUserModel
from src.user.auth.services.verification_notifier import VerificationNotifier
from src.user.schemas import UserProfileViewModel

logger = get_logger(__name__)


class RegisterUseCase:
    """Use case for user registration."""

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
        self, data: CreateUserModel, request_base_url: URL
    ) -> UserProfileViewModel:
        async with self.uow as uow:
            user = await uow.users.create(
                session=uow.session,
                data=data.model_dump(),
            )
            await uow.session.flush()

            notifier = VerificationNotifier(
                email_service=self.email_service, redis_client=self.redis_client
            )
            throttle_key = build_email_throttle_key("signup", user.email)
            await notifier.send_verification(
                user=user,
                base_url=request_base_url,
                throttle_key=throttle_key,
            )
            await uow.commit()
            logger.info(
                "[Register User] User '%s' registered successfully.", data.username
            )
            return UserProfileViewModel.model_validate(user)


def get_register_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    email_service: EmailService = Depends(get_email_service),
    redis_client: Redis = Depends(get_redis_client),
) -> RegisterUseCase:
    return RegisterUseCase(
        uow=uow, email_service=email_service, redis_client=redis_client
    )
