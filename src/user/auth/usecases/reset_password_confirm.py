from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import hash_password, mask_email
from src.main.config import config
from src.user.auth.schemas import ResetPasswordModel
from src.user.auth.token_helpers import invalidate_all_user_sessions

logger = get_logger(__name__)


class ResetPasswordConfirmUseCase:
    """
    Confirm password reset using a valid JWT reset token.

    Inputs:
    - data: ResetPasswordModel containing the token and the new password.

    Validations:
    - Token must be valid and not expired.
    - Token mode must be 'reset_password_token'.
    - Email must be present in the token.
    - User must exist in the database.

    Workflow:
    1) Decode and validate the JWT reset token.
    2) Extract email from the token payload.
    3) Hash and update the user's password in the database.
    4) Invalidate all active sessions for the user in Redis.
    5) Commit the transaction.

    Side effects:
    - Updates user record in the database.
    - Deletes user session keys from Redis.

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

                    user = await uow.users.update(
                        uow.session,
                        {"password_hash": hash_password(data.password)},
                        email=email,
                    )
                    await uow.session.flush()
                    if not user:
                        logger.info(
                            "[ResetPasswordConfirm] User with email %s not found.",
                            mask_email(email),
                        )
                        return SuccessResponse(success=False)

                    await invalidate_all_user_sessions(str(user.id), self.redis_client)
                    logger.debug(
                        "[ResetPasswordConfirm] All user %s sessions invalidated.",
                        mask_email(email),
                    )
                    await uow.commit()
                    logger.info(
                        "[ResetPasswordConfirm] Successfully changed password for user "
                        "with email %s.",
                        mask_email(email),
                    )
                    return SuccessResponse(success=True)
                else:
                    logger.info("[ResetPasswordConfirm] Invalid token mode.")
                    return SuccessResponse(success=False)

            except jwt.ExpiredSignatureError:
                logger.info("[ResetPasswordConfirm] Token has expired.")
                return SuccessResponse(success=False)

            except jwt.InvalidTokenError:
                logger.info("[ResetPasswordConfirm] Token is invalid. Ema")
                return SuccessResponse(success=False)


def get_reset_password_confirm_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    redis_client: Redis = Depends(get_redis_client),
) -> ResetPasswordConfirmUseCase:
    return ResetPasswordConfirmUseCase(
        uow=uow,
        redis_client=redis_client,
    )
