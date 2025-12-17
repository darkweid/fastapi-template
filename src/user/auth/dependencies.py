from typing import cast

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
import jwt
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.models import User
from src.user.repositories import UserRepository

access_token_header = APIKeyHeader(name="Authorization", scheme_name="access-token")
refresh_token_header = APIKeyHeader(name="Authorization", scheme_name="refresh-token")


async def get_current_user(
    token: str = Security(access_token_header),
    session: AsyncSession = Depends(get_session),
    redis_client: Redis = Depends(get_redis_client),
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
    payload = await verify_jti(token, redis_client)

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
    redis_client: Redis = Depends(get_redis_client),
) -> tuple[User, JWTPayload]:
    """
    Get the user from a refresh token for generating a new access token.

    Args:
        refresh_token: The JWT refresh token
        session: Database session

    Returns:
        tuple[User, JWTPayload]: The authenticated user and token payload

    Raises:
        UnauthorizedException: If authentication fails
    """
    credentials_exception = UnauthorizedException(
        "Could not validate credentials",
    )

    # verify_jti also validates the token and throws appropriate exceptions
    payload = await verify_jti(refresh_token, redis_client)

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

    return user, payload


async def get_user_id_from_token(
    request: Request,
) -> str:
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

    redis_client = await get_redis_client(request)
    payload = await verify_jti(token, redis_client)
    try:
        identifier = payload["sub"]

        return identifier
    except KeyError:
        raise UnauthorizedException(
            "Invalid or expired token",
        )


async def verify_jti(token: str, redis_client: Redis) -> JWTPayload:
    """
    Verifies the JWT token's JTI (JWT ID) against the stored value in Redis.

    This function is a central part of the token security system and performs
    several important checks:

    1. Validates the token signature and expiration
    2. Extracts and validates required token fields
    3. For refresh tokens:
       - Checks if the token has been used before (reuse detection)
       - Verifies that the token belongs to a valid token family
    4. For all tokens:
       - Verifies that the token's JTI matches the one stored in Redis

    The function works closely with the token rotation mechanism to provide
    robust protection against token theft and replay attacks.

    Args:
        token: The JWT token to verify (with or without 'Bearer ' prefix)

    Returns:
        JWTPayload: The verified JWT payload with all claims

    Raises:
        UnauthorizedException: If the token is invalid, expired, has been reused,
                               belongs to an invalid family, or has been invalidated
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

    # Check for reuse
    if mode == "refresh_token":
        used_key = f"used:{user_id}:{jti}"
        is_used = await redis_client.exists(used_key)

        if is_used:
            # Token reuse detected!
            from src.user.auth.token_helpers import invalidate_all_user_sessions

            await invalidate_all_user_sessions(user_id, redis_client)
            raise UnauthorizedException(
                "Token reuse detected. All sessions invalidated."
            )

        # Token family validation
        family = payload_typed.get("family")
        if family:
            family_key = f"family:{user_id}:{family}"
            family_exists = await redis_client.exists(family_key)
            if not family_exists:
                from src.user.auth.token_helpers import invalidate_all_user_sessions

                await invalidate_all_user_sessions(user_id, redis_client)
                raise UnauthorizedException(
                    "Token family invalidated. All sessions terminated."
                )

    # Check active tokens
    prefix = mode.replace("_token", "")
    active_key = f"{prefix}:{user_id}:{session_id}"
    stored_jti = await redis_client.get(active_key)

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
