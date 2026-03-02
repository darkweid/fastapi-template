from uuid import uuid4

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import (
    InstanceProcessingException,
    PermissionDeniedException,
)
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.core.utils.security import (
    hash_password,
    mask_email,
    needs_password_rehash,
    verify_password,
)
from src.user.auth.schemas import LoginUserModel
from src.user.auth.security import create_access_token, create_refresh_token
from src.user.models import User

INVALID_CREDENTIALS_MESSAGE = "Incorrect email or password."
INVALID_CREDENTIALS_PASSWORD_HASH = hash_password("dummy-password")
logger = get_logger(__name__)


class LoginUserUseCase:
    """
    Log in a user and return access and refresh tokens.

    Inputs:
    - data: LoginUserModel containing email and password.

    Validations:
    - User must exist.
    - Password must be correct.
    - User must be verified.
    - User must be active (not blocked).

    Workflow:
    1) Retrieve user by email.
    2) Verify password (using dummy hash if user not found to prevent timing attacks).
    3) Check if user is verified.
    4) Check if user is active.
    5) Rehash and persist the password if needed.
    6) Generate access and refresh tokens.

    Side effects:
    - Persists password hash updates when rehashing is required.
    - Token creation handles its own caching.

    Errors:
    - InstanceProcessingException: if credentials are invalid or the user is not verified.
    - PermissionDeniedException: if the user is blocked.

    Returns:
    - TokenModel with access and refresh tokens.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        redis_client: Redis,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client

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
                await verify_password(data.password, INVALID_CREDENTIALS_PASSWORD_HASH)
                raise InstanceProcessingException(INVALID_CREDENTIALS_MESSAGE)

            correct_password = await verify_password(data.password, user.password_hash)
            if not correct_password:
                logger.debug(
                    "[LoginUser] Incorrect password for user '%s'",
                    mask_email(data.email),
                )
                raise InstanceProcessingException(INVALID_CREDENTIALS_MESSAGE)

            if not user.is_verified:
                logger.debug(
                    "[LoginUser] User with email '%s' not verified.",
                    mask_email(data.email),
                )
                raise InstanceProcessingException("User is not verified")

            if not user.is_active:
                logger.debug(
                    "[LoginUser] User with email '%s' is blocked.",
                    mask_email(data.email),
                )
                raise PermissionDeniedException("User is blocked")

            await self._rehash_password_if_needed(uow, user, data.password)
            await uow.flush()
            token_data = {"sub": str(user.id)}

            session_id = str(uuid4())
            family = str(uuid4())
            await uow.commit()
            return TokenModel(
                access_token=await create_access_token(
                    token_data, redis_client=self.redis_client, session_id=session_id
                ),
                refresh_token=await create_refresh_token(
                    token_data,
                    redis_client=self.redis_client,
                    session_id=session_id,
                    family=family,
                ),
            )

    async def _rehash_password_if_needed(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        user: User,
        raw_password: str,
    ) -> None:
        if not needs_password_rehash(user.password_hash):
            return
        await uow.users.update(
            uow.session,
            {"password_hash": hash_password(raw_password)},
            id=user.id,
        )


def get_login_user_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    redis_client: Redis = Depends(get_redis_client),
) -> LoginUserUseCase:
    return LoginUserUseCase(
        uow=uow,
        redis_client=redis_client,
    )
