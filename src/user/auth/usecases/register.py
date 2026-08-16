from typing import Annotated

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.utils.security import hash_password
from src.user.auth.schemas import CreateUserModel
from src.user.auth.services.email_notifier import (
    EmailNotifier,
    get_verification_notifier,
)
from src.user.schemas import UserProfileViewModel

logger = get_logger(__name__)


class RegisterUseCase:
    """
    Register a new user and send a verification email.

    Inputs:
    - data: CreateUserModel containing user registration details.

    Validations:
    - Email and username must be unique (handled by DB constraints/repository).

    Workflow:
    1) Create a new user record in the database.
    2) Store the verification email delivery in the outbox within the same
       transaction.
    3) Commit the transaction (the outbox publish hook fires after commit).

    Side effects:
    - Creates a user record in the database.
    - Inserts an outbox row for the verification email and publishes it
      after commit.

    Errors:
    - Duplicate email/username surfaces as IntegrityError and is answered 409
      with code "already_exists" by the database error middleware.

    Returns:
    - UserProfileViewModel: the newly created user profile.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        notifier: EmailNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(self, data: CreateUserModel) -> UserProfileViewModel:
        async with self.uow as uow:
            user_data = data.model_dump()
            raw_password = user_data.pop("password")
            user_data["password_hash"] = hash_password(raw_password)
            user = await uow.users.create(
                session=uow.session,
                data=user_data,
            )
            # Outbox row rides the same transaction: a rollback cancels the
            # email, a broker outage no longer fails registration.
            await self.notifier.send(uow=uow, user=user)
            await uow.commit()

        logger.info("[Register User] User '%s' registered successfully.", data.username)
        return UserProfileViewModel.model_validate(user)


def get_register_use_case(
    uow: Annotated[
        ApplicationUnitOfWork[RepositoryProtocol], Depends(get_unit_of_work)
    ],
    notifier: Annotated[EmailNotifier, Depends(get_verification_notifier)],
) -> RegisterUseCase:
    return RegisterUseCase(uow=uow, notifier=notifier)
