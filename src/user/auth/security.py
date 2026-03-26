from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
from redis.asyncio import Redis

from src.core.utils.datetime_utils import get_utc_now
from src.core.utils.security import normalize_email
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.token_helpers import (
    execute_token_rotation,
    store_active_one_time_token,
    validate_token_structure,
)


async def create_access_token(
    data: dict[str, Any], redis_client: Redis, session_id: str | None = None
) -> str:
    """
    Create a new JWT access token

    Args:
        data: Dictionary containing token data (must include 'sub' key with user ID)
        session_id: Optional session ID for tracking multiple sessions per user
    Returns:
        str: Encoded JWT access token
    """
    jti = str(uuid4())
    if session_id is None:
        session_id = str(uuid4())
    expire = get_utc_now() + timedelta(minutes=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": data["sub"],
        "exp": int(expire.timestamp()),
        "mode": "access_token",
        "jti": jti,
        "session_id": session_id,
    }

    encoded_jwt = jwt.encode(
        dict(payload), config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        auth_redis_keys.access(data["sub"], session_id),
        jti,
        ex=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    return str(encoded_jwt)


async def create_refresh_token(
    data: dict[str, Any],
    redis_client: Redis,
    session_id: str | None = None,
) -> str:
    """
    Create a new JWT refresh token

    Args:
        data: Dictionary containing token data (must include 'sub' key with user ID)
        session_id: Optional session ID for tracking multiple sessions per user
    Returns:
        str: Encoded JWT refresh token
    """
    jti = str(uuid4())
    if session_id is None:
        session_id = str(uuid4())

    expire = get_utc_now() + timedelta(minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": data["sub"],
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti,
        "session_id": session_id,
    }

    encoded_jwt = jwt.encode(
        dict(payload), config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        auth_redis_keys.refresh(data["sub"], session_id),
        jti,
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    return str(encoded_jwt)


async def rotate_refresh_token(old_payload: JWTPayload, redis_client: Redis) -> str:
    """
    Rotate a refresh token by creating a new one while invalidating the old one.

    This function implements the token rotation pattern for refresh tokens:
    1. Validate token structure and extract the necessary fields
    2. Atomically invalidate the old token and mark it as used
    3. Create a new token in the same logical session

    Args:
        old_payload: The payload from the old refresh token

    Returns:
        str: A new refresh token

    Raises:
        UnauthorizedException: If the token is invalid, has been reused, or has other security issues
    """

    # Extract and validate token fields
    user_id, old_session_id, old_jti = await validate_token_structure(
        old_payload, redis_client
    )

    await execute_token_rotation(user_id, old_session_id, old_jti, redis_client)

    # Rotate the refresh token within the same logical session.
    jti = str(uuid4())
    expire = get_utc_now() + timedelta(minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": user_id,
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti,
        "session_id": old_session_id,
    }
    token_data: dict[str, Any] = dict(payload)

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        auth_redis_keys.refresh(user_id, old_session_id),
        jti,
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    return str(encoded_jwt)


async def create_verification_token(data: dict[str, Any], redis_client: Redis) -> str:
    """
    Create a new JWT verification token and store its active JTI in Redis.

    Args:
        data: Dictionary containing token data (must include 'email' key)
        redis_client: Redis client used for active JTI tracking

    Returns:
        str: Encoded JWT verification token
    """
    email = normalize_email(str(data.get("email", "")))
    jti = str(uuid4())
    expire = get_utc_now() + timedelta(
        minutes=config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES
    )

    payload: JWTPayload = {
        "sub": email,
        "exp": int(expire.timestamp()),
        "mode": "verification_token",
        "jti": jti,
    }

    token_data = {**data, "email": email, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM
    )

    await store_active_one_time_token(
        purpose="verification",
        email=email,
        jti=jti,
        ttl_seconds=config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES * 60,
        redis_client=redis_client,
    )

    return str(encoded_jwt)


async def create_reset_password_token(data: dict[str, Any], redis_client: Redis) -> str:
    """
    Create a new JWT password-reset token and store its active JTI in Redis.

    Args:
        data: Dictionary containing token data (must include 'email' key)
        redis_client: Redis client used for active JTI tracking

    Returns:
        str: Encoded JWT password reset token
    """
    email = normalize_email(str(data.get("email", "")))
    jti = str(uuid4())
    expire = get_utc_now() + timedelta(
        minutes=config.jwt.RESET_PASSWORD_TOKEN_EXPIRE_MINUTES
    )

    payload: JWTPayload = {
        "sub": email,
        "exp": int(expire.timestamp()),
        "mode": "reset_password_token",
        "jti": jti,
    }

    token_data = {**data, "email": email, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_RESET_PASSWORD_SECRET_KEY, config.jwt.ALGORITHM
    )

    await store_active_one_time_token(
        purpose="reset_password",
        email=email,
        jti=jti,
        ttl_seconds=config.jwt.RESET_PASSWORD_TOKEN_EXPIRE_MINUTES * 60,
        redis_client=redis_client,
    )

    return str(encoded_jwt)
