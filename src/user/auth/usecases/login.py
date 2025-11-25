from uuid import uuid4

from fastapi import Depends

from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import (
    InstanceProcessingException,
    PermissionDeniedException,
)
from src.core.utils.security import verify_password, mask_email
from src.user.auth.schemas import LoginUserModel
from src.core.schemas import TokenModel
from src.user.auth.security import create_access_token, create_refresh_token
from loggers import get_logger

logger = get_logger(__name__)


class LoginUserUseCase:
    """Use case for logging in user."""

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
    ) -> None:
        self.uow = uow

    async def execute(
        self,
        data: LoginUserModel,
    ) -> TokenModel:
        async with self.uow as uow:
            user = await uow.users.get_single(uow.session, email=data.email)
            if not user:
                logger.debug(
                    "[LoginUser] User with email '%s' not found.",
                    mask_email(data.email),
                )
                raise InstanceProcessingException("Incorrect Password or Email")

            correct_password = await verify_password(data.password, user.password)
            if not correct_password:
                logger.debug(
                    "[LoginUser] Incorrect password for user '%s'",
                    mask_email(data.email),
                )
                raise InstanceProcessingException("Incorrect Password or Email")

            if not user.is_verified:
                logger.info(
                    "[LoginUser] User with email '%s' not verified.",
                    mask_email(data.email),
                )
                raise InstanceProcessingException("User is not verified")

            if not user.is_active:
                logger.info(
                    "[LoginUser] User with email '%s' is blocked.",
                    mask_email(data.email),
                )
                raise PermissionDeniedException("User is blocked")

            token_data = {"sub": str(user.id)}

            session_id = str(uuid4())
            family = str(uuid4())

            return TokenModel(
                access_token=await create_access_token(
                    token_data, session_id=session_id
                ),
                refresh_token=await create_refresh_token(
                    token_data, session_id=session_id, family=family
                ),
            )


def get_login_user_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
) -> LoginUserUseCase:
    return LoginUserUseCase(
        uow=uow,
    )
