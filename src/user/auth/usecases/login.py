from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.core.utils.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    mask_email,
    needs_password_rehash,
    verify_password,
)
from src.user.auth.errors import InvalidCredentialsError
from src.user.auth.schemas import LoginUserModel
from src.user.auth.security import create_access_token, create_refresh_token
from src.user.cache_keys import user_cache_keys
from src.user.models import User
from src.user.policies import (
    INVALID_CREDENTIALS_MESSAGE,
    account_access_violation,
    ensure_can_authenticate,
)

logger = get_logger(__name__)


class LoginUserUseCase:
    """
    Log in a user and return access and refresh tokens.

    Inputs:
    - data: LoginUserModel containing email and password.

    Validations:
    - User must exist.
    - Password must be correct.
    - Account must pass admission (verified and active) - see policies.py.

    Workflow:
    1) Retrieve user by email.
    2) Verify password (using dummy hash if user not found to prevent timing attacks).
    3) Check account admission; a violation is masked into the same error as
       bad credentials (anti-enumeration).
    4) Rehash and persist the password if needed.
    5) Invalidate the user cache namespace.
    6) Commit the transaction and generate access and refresh tokens.

    Side effects:
    - Persists password hash updates when rehashing is required.
    - Bumps the user:{id} cache namespace version - every write to the user row
      does this unconditionally, including this one where the row is only
      sometimes touched (the rehash branch), so no one has to remember an
      exception to the rule.
    - Token creation handles its own caching.

    Errors:
    - InvalidCredentialsError: wrong credentials, unverified or blocked account -
      one code by design (anti-enumeration).

    Returns:
    - TokenModel with access and refresh tokens.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork,
        redis_client: Redis,
        cache: Cache,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client
        self.cache = cache

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
                await verify_password(data.password, DUMMY_PASSWORD_HASH)
                raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

            correct_password = await verify_password(data.password, user.password_hash)
            if not correct_password:
                logger.debug(
                    "[LoginUser] Incorrect password for user '%s'",
                    mask_email(data.email),
                )
                raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)

            violation = account_access_violation(user)
            if violation is not None:
                logger.debug(
                    "[LoginUser] Account of '%s' fails admission (%s).",
                    mask_email(data.email),
                    violation,
                )
            ensure_can_authenticate(user)

            await self._rehash_password_if_needed(uow, user, data.password)
            session_id = str(uuid4())
            token_data = {"sub": str(user.id)}
            await self.cache.invalidate(user_cache_keys.namespace(user.id))
            await uow.commit()
            return TokenModel(
                access_token=await create_access_token(
                    token_data, redis_client=self.redis_client, session_id=session_id
                ),
                refresh_token=await create_refresh_token(
                    token_data,
                    redis_client=self.redis_client,
                    session_id=session_id,
                ),
            )

    async def _rehash_password_if_needed(
        self,
        uow: ApplicationUnitOfWork,
        user: User,
        raw_password: str,
    ) -> None:
        if not needs_password_rehash(user.password_hash):
            return
        new_hash = await hash_password(raw_password)
        await uow.users.update(
            uow.session,
            {"password_hash": new_hash},
            id=user.id,
        )


def get_login_user_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> LoginUserUseCase:
    return LoginUserUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
