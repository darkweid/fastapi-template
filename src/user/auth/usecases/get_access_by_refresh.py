import jwt

from src.core.errors.exceptions import (
    InstanceProcessingException,
    PermissionDeniedException,
)
from src.core.schemas import TokenModel
from src.core.utils.security import mask_email
from src.main.config import config
from src.user.auth.security import create_access_token, rotate_refresh_token
from src.user.auth.jwt_payload_schema import JWTPayload
from loggers import get_logger
from src.user.models import User

logger = get_logger(__name__)


class GetTokensByRefreshUserUseCase:
    """Use case for refreshing tokens using a refresh token."""

    async def execute(
        self,
        user: User,
        old_token_payload: JWTPayload,
    ) -> TokenModel:
        if not user.is_active:
            logger.info(
                "[RefreshTokens] Blocked user '%s' attempted refresh",
                mask_email(user.email),
            )
            raise PermissionDeniedException("User is blocked")

        if not user.is_verified:
            logger.info(
                "[RefreshTokens] Unverified user '%s' attempted refresh",
                mask_email(user.email),
            )
            raise InstanceProcessingException("User is not verified")

        # Use rotation helper to handle the previous token safely
        new_refresh_token = await rotate_refresh_token(old_token_payload)

        new_payload = jwt.decode(
            new_refresh_token,
            config.jwt.JWT_USER_SECRET_KEY,
            algorithms=[config.jwt.ALGORITHM],
        )

        access_token = await create_access_token(
            {"sub": str(user.id)}, session_id=new_payload["session_id"]
        )

        return TokenModel(
            access_token=access_token,
            refresh_token=new_refresh_token,
        )


def get_tokens_by_refresh_user_use_case() -> GetTokensByRefreshUserUseCase:
    return GetTokensByRefreshUserUseCase()
