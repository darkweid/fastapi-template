from typing import Annotated

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
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
    Register a user and send the verification email.

    The email is written to the outbox inside the same transaction as the user
    row, so a committed registration always has a queued email and a rolled
    back one never leaves a stray send. A duplicate email or username reaches
    the database error middleware as an IntegrityError and is answered 409
    `already_exists` there, not here.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        notifier: EmailNotifier,
    ) -> None:
        self.uow = uow
        self.notifier = notifier

    async def execute(self, data: CreateUserModel) -> UserProfileViewModel:
        async with self.uow as uow:
            user_data = data.model_dump()
            raw_password = user_data.pop("password")
            user_data["password_hash"] = await hash_password(raw_password)
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
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    notifier: Annotated[EmailNotifier, Depends(get_verification_notifier)],
) -> RegisterUseCase:
    return RegisterUseCase(uow=uow, notifier=notifier)
