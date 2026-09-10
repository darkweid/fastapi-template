from functools import partial
from typing import Annotated

from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.one_time_tokens import decode_one_time_token
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email
from src.user.auth.realm import USER_AUTH_REALM, VERIFICATION_PURPOSE
from src.user.cache_keys import user_cache_keys
from src.user.policies import verification_pending

logger = get_logger(__name__)


class VerifyEmailUseCase:
    """
    Verify an email address with a single-use token.

    An invalid, expired or superseded token, and an email naming no user,
    answer success=False instead of raising - the endpoint must not confirm who
    has an account. A second click on an already used link lands there too:
    decoding consumes the challenge, so the link no longer decodes. The
    already-verified branch covers the other case - a token still live for an
    account that got verified some other way - and answers success for it.

    The token is spent before the transaction runs, which is what stops two
    concurrent clicks from both proceeding; a failed commit therefore burns the
    link and the user asks for a new one.
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
                    realm=USER_AUTH_REALM,
                    purpose=VERIFICATION_PURPOSE,
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
