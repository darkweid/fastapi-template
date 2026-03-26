from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import hash_password, mask_email, normalize_email
from src.main.config import config
from src.user.auth.schemas import ResetPasswordModel
from src.user.auth.token_helpers import (
    invalidate_active_one_time_token,
    invalidate_all_user_sessions,
    validate_active_one_time_token,
)

logger = get_logger(__name__)


class ResetPasswordConfirmUseCase:
    """
    Confirm password reset using a valid JWT reset token.

    Inputs:
    - data: ResetPasswordModel containing the token and the new password.

    Validations:
    - Token must be valid and not expired.
    - Token mode must be 'reset_password_token'.
    - Token JTI must match the active Redis entry for the email.
    - Email must be present in the token.
    - User must exist in the database.

    Workflow:
    1) Decode and validate the JWT reset token.
    2) Extract email and validate the active JTI in Redis.
    3) Hash and update the user's password in the database.
    4) Flush pending DB changes.
    5) Delete the active reset-token key and invalidate all user sessions.
    6) Commit the transaction.

    Side effects:
    - Updates user record in the database.
    - Deletes the active reset-token key from Redis before commit to avoid
      partial-success password changes when Redis is unavailable.
    - Deletes user session keys from Redis before commit for the same reason.

    Errors:
    - None (returns success=False for invalid tokens/users).

    Returns:
    - SuccessResponse: success=True if password was reset, False otherwise.
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
        data: ResetPasswordModel,
    ) -> SuccessResponse:
        async with self.uow as uow:
            try:
                payload = jwt.decode(
                    data.token,
                    config.jwt.JWT_RESET_PASSWORD_SECRET_KEY,
                    [config.jwt.ALGORITHM],
                )

                if payload.get("mode") == "reset_password_token":
                    email = payload.get("email")
                    if not email:
                        logger.info("[ResetPasswordConfirm] Email not found in token.")
                        return SuccessResponse(success=False)

                    normalized_email = normalize_email(email)
                    await validate_active_one_time_token(
                        purpose="reset_password",
                        email=normalized_email,
                        jti=payload.get("jti"),
                        redis_client=self.redis_client,
                    )

                    user = await uow.users.update(
                        uow.session,
                        {"password_hash": hash_password(data.password)},
                        email=normalized_email,
                    )
                    if not user:
                        logger.info(
                            "[ResetPasswordConfirm] User with email %s not found.",
                            mask_email(normalized_email),
                        )
                        return SuccessResponse(success=False)

                    await uow.flush()
                    await invalidate_active_one_time_token(
                        purpose="reset_password",
                        email=normalized_email,
                        redis_client=self.redis_client,
                    )
                    await invalidate_all_user_sessions(str(user.id), self.redis_client)
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
                else:
                    logger.info("[ResetPasswordConfirm] Invalid token mode.")
                    return SuccessResponse(success=False)

            except jwt.ExpiredSignatureError:
                logger.info("[ResetPasswordConfirm] Token has expired.")
                return SuccessResponse(success=False)

            except UnauthorizedException:
                logger.info("[ResetPasswordConfirm] Token JTI is inactive or invalid.")
                return SuccessResponse(success=False)

            except jwt.InvalidTokenError:
                logger.info("[ResetPasswordConfirm] Token is invalid.")
                return SuccessResponse(success=False)


def get_reset_password_confirm_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    redis_client: Redis = Depends(get_redis_client),
) -> ResetPasswordConfirmUseCase:
    return ResetPasswordConfirmUseCase(
        uow=uow,
        redis_client=redis_client,
    )
