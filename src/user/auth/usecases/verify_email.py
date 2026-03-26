from fastapi import Depends
import jwt
from redis.asyncio import Redis

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email, normalize_email
from src.main.config import config
from src.user.auth.token_helpers import (
    invalidate_active_one_time_token,
    validate_active_one_time_token,
)

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
    6) Commit the transaction.
    7) Consume the token.

    Side effects:
    - Updates user record in the database.
    - Deletes the active verification-token key from Redis after successful use.

    Errors:
    - UnauthorizedException: if token is invalid or expired.

    Returns:
    - SuccessResponse: success=True if verified or already verified, False if email/user not found.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
        redis_client: Redis,
    ) -> None:
        self.uow = uow
        self.redis_client = redis_client

    async def execute(self, token: str) -> SuccessResponse:
        async with self.uow as uow:
            try:
                payload = jwt.decode(
                    token, config.jwt.JWT_VERIFY_SECRET_KEY, [config.jwt.ALGORITHM]
                )
                email: str | None = payload.get("email")
                if not email:
                    logger.debug("[VerifyEmail] Email not found in token")
                    return SuccessResponse(success=False)

                normalized_email = normalize_email(email)
                await validate_active_one_time_token(
                    purpose="verification",
                    email=normalized_email,
                    jti=payload.get("jti"),
                    redis_client=self.redis_client,
                )

                user = await uow.users.get_single(uow.session, email=normalized_email)
                if not user:
                    logger.debug(
                        "[VerifyEmail] User with email '%s' not found.",
                        mask_email(normalized_email),
                    )
                    return SuccessResponse(success=False)
                if user.is_verified:
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

            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                raise UnauthorizedException(
                    "Invalid or expired token.",
                )


def get_verify_email_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
    redis_client: Redis = Depends(get_redis_client),
) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        uow=uow,
        redis_client=redis_client,
    )
