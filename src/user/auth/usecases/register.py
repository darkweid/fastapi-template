from fastapi import Depends
from starlette.datastructures import URL

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.utils.security import hash_password
from src.user.auth.schemas import CreateUserModel
from src.user.auth.services.verification_notifier import (
    VerificationNotifier,
    get_verification_notifier,
)
from src.user.schemas import UserProfileViewModel

logger = get_logger(__name__)


class RegisterUseCase:
    """
    Register a new user and send a verification email.

    Inputs:
    - data: CreateUserModel containing user registration details.
    - request_base_url: The base URL of the request for building verification links.

    Validations:
    - Email and username must be unique (handled by DB constraints/repository).

    Workflow:
    1) Create a new user record in the database.
    2) Commit the transaction.
    3) Queue verification email delivery.

    Side effects:
    - Creates a user record in the database.
    - Queues an external email notification after a successful commit.

    Errors:
    - InstanceAlreadyExistsException: if email or username already exists.

    Returns:
    - UserProfileViewModel: the newly created user profile.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        notifier: VerificationNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(
        self, data: CreateUserModel, request_base_url: URL
    ) -> UserProfileViewModel:
        async with self.uow as uow:
            user_data = data.model_dump()
            raw_password = user_data.pop("password")
            user_data["password_hash"] = hash_password(raw_password)
            user = await uow.users.create(
                session=uow.session,
                data=user_data,
            )
            await uow.commit()
            try:
                await self.notifier.send_verification(
                    user=user,
                    base_url=request_base_url,
                )
            except Exception:
                logger.exception(
                    "[Register User] Failed to queue verification email for '%s'.",
                    data.username,
                )
            logger.info(
                "[Register User] User '%s' registered successfully.", data.username
            )
            return UserProfileViewModel.model_validate(user)


def get_register_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    notifier: VerificationNotifier = Depends(get_verification_notifier),
) -> RegisterUseCase:
    return RegisterUseCase(uow=uow, notifier=notifier)
