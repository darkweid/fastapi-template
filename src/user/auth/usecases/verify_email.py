from functools import partial
from typing import Annotated

from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import Cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email
from src.main.config import config
from src.user.auth.security import decode_one_time_token
from src.user.auth.token_helpers import invalidate_active_one_time_token
from src.user.cache_keys import user_cache_keys
from src.user.policies import verification_pending

logger = get_logger(__name__)


class VerifyEmailUseCase:
    """
    Verify a user's email address using a JWT token.

    Inputs:
    - token: JWT token containing the user's email.

    Validations:
    - Token must be valid and not expired.
    - Token JTI must match the active Redis entry for the email.
    - Email must be present in the token.
    - User must exist in the database.

    Workflow:
    1) Decode and validate the JWT token.
    2) Extract email and validate the active JTI in Redis.
    3) Retrieve user by normalized email.
    4) If user is already verified, consume the token and return success.
    5) Update user's is_verified status to True.
    6) Invalidate the user cache namespace.
    7) Commit the transaction.
    8) Consume the token.

    Side effects:
    - Updates user record in the database.
    - Deletes the active verification-token key from Redis after successful use.
    - Bumps the user:{id} cache namespace version twice (pre- and post-commit).

    Returns:
    - SuccessResponse: success=True if verified or already verified, False if the
      token is invalid/inactive or the email/user is not found.
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

    async def execute(self, token: str) -> SuccessResponse:
        async with self.uow as uow:
            try:
                normalized_email = await decode_one_time_token(
                    token,
                    secret=config.jwt.JWT_VERIFY_SECRET_KEY,
                    purpose="verification",
                    redis_client=self.redis_client,
                    expected_mode="verification_token",
                )

                user = await uow.users.get_single(uow.session, email=normalized_email)
                if not user:
                    logger.debug(
                        "[VerifyEmail] User with email '%s' not found.",
                        mask_email(normalized_email),
                    )
                    return SuccessResponse(success=False)
                if not verification_pending(user):
                    await invalidate_active_one_time_token(
                        purpose="verification",
                        email=normalized_email,
                        redis_client=self.redis_client,
                    )
                    logger.debug(
                        "[VerifyEmail] User with email '%s' already verified.",
                        mask_email(normalized_email),
                    )
                    return SuccessResponse(success=True)

                await uow.users.update(
                    uow.session,
                    {"is_verified": True},
                    email=normalized_email,
                )
                await self.cache.invalidate(user_cache_keys.namespace(user.id))
                uow.add_after_commit_hook(
                    partial(self.cache.invalidate, user_cache_keys.namespace(user.id))
                )
                await uow.commit()
                await invalidate_active_one_time_token(
                    purpose="verification",
                    email=normalized_email,
                    redis_client=self.redis_client,
                )

                logger.info(
                    "[VerifyEmail] User with email '%s' verified successfully.",
                    mask_email(normalized_email),
                )
                return SuccessResponse(success=True)

            except UnauthorizedException:
                logger.info("[VerifyEmail] Token JTI is inactive or invalid.")
                return SuccessResponse(success=False)

            except jwt.ExpiredSignatureError:
                logger.info("[VerifyEmail] Token has expired.")
                return SuccessResponse(success=False)

            except jwt.InvalidTokenError:
                logger.info("[VerifyEmail] Token is invalid.")
                return SuccessResponse(success=False)


def get_verify_email_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
