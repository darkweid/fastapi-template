from fastapi import Depends
import jwt

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import SuccessResponse
from src.core.utils.security import mask_email, normalize_email
from src.main.config import config

logger = get_logger(__name__)


class VerifyEmailUseCase:
    """
    Verify a user's email address using a JWT token.

    Inputs:
    - token: JWT token containing the user's email.

    Validations:
    - Token must be valid and not expired.
    - Email must be present in the token.
    - User must exist in the database.

    Workflow:
    1) Decode and validate the JWT token.
    2) Extract email from the token payload.
    3) Retrieve user by normalized email.
    4) If user is already verified, return success.
    5) Update user's is_verified status to True.
    6) Commit the transaction.

    Side effects:
    - Updates user record in the database.

    Errors:
    - UnauthorizedException: if token is invalid or expired.

    Returns:
    - SuccessResponse: success=True if verified or already verified, False if email/user not found.
    """

    def __init__(
        self,
        uow: ApplicationUnitOfWork[RepositoryProtocol],
    ) -> None:
        self.uow = uow

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

                user = await uow.users.get_single(
                    uow.session, email=normalize_email(email)
                )
                if not user:
                    logger.debug(
                        "[VerifyEmail] User with email '%s' not found.",
                        mask_email(email),
                    )
                    return SuccessResponse(success=False)
                if user.is_verified:
                    logger.debug(
                        "[VerifyEmail] User with email '%s' already verified.",
                        mask_email(email),
                    )
                    return SuccessResponse(success=True)

                await uow.users.update(
                    uow.session,
                    {"is_verified": True},
                    email=payload.get("email"),
                )
                await uow.flush()

                await uow.commit()
                logger.info(
                    "[VerifyEmail] User with email '%s' verified successfully.",
                    mask_email(email),
                )
                return SuccessResponse(success=True)

            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                raise UnauthorizedException(
                    "Invalid or expired token.",
                )


def get_verify_email_use_case(
    uow: ApplicationUnitOfWork[RepositoryProtocol] = Depends(get_unit_of_work),
) -> VerifyEmailUseCase:
    return VerifyEmailUseCase(
        uow=uow,
    )
