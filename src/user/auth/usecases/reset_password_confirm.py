from functools import partial
from typing import Annotated

from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.auth.one_time_tokens import decode_one_time_token
from src.core.auth.token_helpers import invalidate_all_sessions
from src.core.cache.interface import Cache
from src.core.cache.runtime import get_cache
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import hash_password, mask_email
from src.user.auth.realm import RESET_PASSWORD_PURPOSE, USER_AUTH_REALM
from src.user.auth.schemas import ResetPasswordModel
from src.user.cache_keys import user_cache_keys

logger = get_logger(__name__)


class ResetPasswordConfirmUseCase:
    """
    Confirm a password reset with a single-use token and store the new password.

    An invalid, expired or superseded token answers success=False instead of
    raising, so a wrong token cannot be told apart from a wrong email.

    Side effects:
    - Deletes the active reset-token key and every session key of the user
      before the commit rather than after: with Redis unavailable the change
      then fails as a whole, instead of leaving a new password alongside
      sessions that still hold the old one.
    - Clears the per-email login-failure counter. The reset proves mailbox
      ownership, so a throttled address must not stay locked out of login.
    - Bumps the user:{id} cache namespace version twice, pre- and post-commit.
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
        self.challenges = ActiveChallengeRegistry(USER_AUTH_REALM)

    async def execute(
        self,
        data: ResetPasswordModel,
    ) -> SuccessResponse:
        async with self.uow as uow:
            try:
                normalized_email = await decode_one_time_token(
                    data.token,
                    realm=USER_AUTH_REALM,
                    purpose=RESET_PASSWORD_PURPOSE,
                    redis_client=self.redis_client,
                    expected_mode="reset_password_token",
                )

                new_password_hash = await hash_password(data.password)
                user = await uow.users.update(
                    uow.session,
                    {"password_hash": new_password_hash},
                    email=normalized_email,
                )
                if not user:
                    logger.info(
                        "[ResetPasswordConfirm] User with email %s not found.",
                        mask_email(normalized_email),
                    )
                    return SuccessResponse(success=False)

                await uow.flush()
                await self.challenges.invalidate(
                    RESET_PASSWORD_PURPOSE, normalized_email, self.redis_client
                )
                await invalidate_all_sessions(
                    str(user.id), self.redis_client, keys=USER_AUTH_REALM.keys
                )
                # A successful reset proves mailbox ownership: clear the
                # login-failure throttle so an attacker who filled the window
                # with wrong passwords cannot keep the real owner locked out.
                await self.redis_client.delete(
                    USER_AUTH_REALM.keys.login_failures(normalized_email)
                )
                await self.cache.invalidate(user_cache_keys.namespace(user.id))
                uow.add_after_commit_hook(
                    partial(self.cache.invalidate, user_cache_keys.namespace(user.id))
                )
                await uow.commit()
                logger.debug(
                    "[ResetPasswordConfirm] All user %s sessions invalidated.",
                    mask_email(normalized_email),
                )
                logger.info(
                    "[ResetPasswordConfirm] Successfully changed password for user "
                    "with email %s.",
                    mask_email(normalized_email),
                )
                return SuccessResponse(success=True)

            except UnauthorizedException:
                logger.info("[ResetPasswordConfirm] Token JTI is inactive or invalid.")
                return SuccessResponse(success=False)

            except jwt.ExpiredSignatureError:
                logger.info("[ResetPasswordConfirm] Token has expired.")
                return SuccessResponse(success=False)

            except jwt.InvalidTokenError:
                logger.info("[ResetPasswordConfirm] Token is invalid.")
                return SuccessResponse(success=False)


def get_reset_password_confirm_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    cache: Annotated[Cache, Depends(get_cache)],
) -> ResetPasswordConfirmUseCase:
    return ResetPasswordConfirmUseCase(
        uow=uow,
        redis_client=redis_client,
        cache=cache,
    )
