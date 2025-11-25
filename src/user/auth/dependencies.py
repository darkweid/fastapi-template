from typing import Literal, NotRequired, TypedDict, cast

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.client import redis_client
from src.main.config import config
from src.user.models import User
from src.user.repositories import UserRepository


class JWTPayload(TypedDict):
    """Type definition for JWT token payload"""

    sub: str  # User ID
    exp: int  # Expiration timestamp
    mode: Literal[
        "access_token", "refresh_token", "verification_token", "reset_password_token"
    ]
    jti: NotRequired[str]  # JWT ID for token tracking
    session_id: NotRequired[str]  # Session identifier


access_token_header = APIKeyHeader(name="Authorization", scheme_name="access-token")
refresh_token_header = APIKeyHeader(name="Authorization", scheme_name="refresh-token")


async def get_current_user(
    token: str = Security(access_token_header),
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    Get the current authenticated user from the access token.

    Args:
        token: The JWT access token
        session: Database session

    Returns:
        User: The authenticated user

    Raises:
        UnauthorizedException: If authentication fails
    """
    credentials_exception = UnauthorizedException(
        "Could not validate credentials",
    )

    # verify_jti also validates the token and throws appropriate exceptions
    payload = await verify_jti(token)

    try:
        user_id = payload["sub"]
        mode = payload["mode"]

        if mode != "access_token":
            raise credentials_exception

    except KeyError:
        raise credentials_exception

    user = await UserRepository().get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return user


async def get_access_by_refresh_token(
    refresh_token: str = Security(refresh_token_header),
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    Get the user from a refresh token for generating a new access token.

    Args:
        refresh_token: The JWT refresh token
        session: Database session

    Returns:
        User: The authenticated user

    Raises:
        UnauthorizedException: If authentication fails
    """
    credentials_exception = UnauthorizedException(
        "Could not validate credentials",
    )

    # verify_jti also validates the token and throws appropriate exceptions
    payload = await verify_jti(refresh_token)

    try:
        user_id = payload["sub"]
        mode = payload["mode"]

        if mode != "refresh_token":
            raise credentials_exception

    except KeyError:
        raise credentials_exception

    user = await UserRepository().get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return user


async def get_user_id_from_token(request: Request) -> str:
    """
    Extracts the user identifier from token provided on Authorization header

    Returns:
        str: The user ID extracted from the token
    """

    token = request.headers.get("Authorization")

    if not token:
        raise UnauthorizedException(
            "Authentication token not found",
        )

    await verify_jti(token)
    try:
        payload = jwt.decode(
            token, config.jwt.JWT_USER_SECRET_KEY, algorithms=[config.jwt.ALGORITHM]
        )
        payload_typed = cast(JWTPayload, payload)
        identifier = payload_typed["sub"]

        return identifier
    except (jwt.PyJWTError, KeyError):
        raise UnauthorizedException(
            "Invalid or expired token",
        )


async def verify_jti(token: str) -> JWTPayload:
    """
    Verifies the JWT token's JTI (JWT ID) against the stored value in Redis.

    Args:
        token: The JWT token to verify

    Returns:
        JWTPayload: The verified JWT payload

    Raises:
        UnauthorizedException: If the token is invalid, expired, or has been invalidated
    """
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        payload = jwt.decode(
            token,
            config.jwt.JWT_USER_SECRET_KEY,
            algorithms=[config.jwt.ALGORITHM],
        )
        payload_typed = cast(JWTPayload, payload)
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException("Token expired")
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid token")

    try:
        jti = payload_typed["jti"]
        mode = payload_typed["mode"]
        user_id = payload_typed["sub"]
        session_id = payload_typed["session_id"]
    except KeyError:
        raise UnauthorizedException("Invalid token structure")

    redis_key = f"{mode.replace('_token', '')}:{user_id}:{session_id}"
    stored_jti = await redis_client.get(redis_key)
    stored_jti_str = (
        stored_jti.decode()
        if isinstance(stored_jti, (bytes, bytearray))
        else stored_jti
    )
    if not stored_jti or stored_jti_str != jti:
        raise UnauthorizedException(
            "Token invalidated or expired",
        )

    return payload_typed
