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
    """Use case for verifying email."""

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
                await uow.session.flush()

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
