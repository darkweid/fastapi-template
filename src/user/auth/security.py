from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
from redis.asyncio import Redis

from src.core.utils.datetime_utils import get_utc_now
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.token_helpers import (
    execute_token_rotation,
    validate_token_family,
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

    # Add any additional data
    token_data = {**data, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        f"access:{data['sub']}:{session_id}",
        jti,
        ex=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

    return str(encoded_jwt)


async def create_refresh_token(
    data: dict[str, Any],
    redis_client: Redis,
    session_id: str | None = None,
    family: str | None = None,
) -> str:
    """
    Create a new JWT refresh token

    Args:
        data: Dictionary containing token data (must include 'sub' key with user ID)
        session_id: Optional session ID for tracking multiple sessions per user
        family: Optional family ID for tracking token lineage

    Returns:
        str: Encoded JWT refresh token
    """
    jti = str(uuid4())
    if session_id is None:
        session_id = str(uuid4())
    if family is None:
        family = str(uuid4())

    expire = get_utc_now() + timedelta(minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": data["sub"],
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti,
        "session_id": session_id,
        "family": family,
    }

    # Add any additional data
    token_data = {**data, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        f"refresh:{data['sub']}:{session_id}",
        jti,
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    # Store family under family:user_id:family_id (not session-specific)
    await redis_client.set(
        f"family:{data['sub']}:{family}",
        "active",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    return str(encoded_jwt)


async def rotate_refresh_token(old_payload: JWTPayload, redis_client: Redis) -> str:
    """
    Rotate a refresh token by creating a new one while invalidating the old one.

    This function implements the token rotation pattern for refresh tokens:
    1. Validate token structure and extract the necessary fields
    2. Check token family validity
    3. Atomically invalidate the old token and mark it as used
    4. Create a new token in the same family
    5. Update the family expiration time

    Args:
        old_payload: The payload from the old refresh token

    Returns:
        str: A new refresh token

    Raises:
        UnauthorizedException: If the token is invalid, has been reused, or has other security issues
    """

    # Extract and validate token fields
    user_id, old_session_id, old_jti, family_id = await validate_token_structure(
        old_payload, redis_client
    )

    await validate_token_family(user_id, family_id, redis_client)

    await execute_token_rotation(user_id, old_session_id, old_jti, redis_client)

    # Create a new refresh token with a new session_id but in the same family
    jti = str(uuid4())
    session_id = str(uuid4())
    expire = get_utc_now() + timedelta(minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": user_id,
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti,
        "session_id": session_id,
        "family": family_id,
    }

    encoded_jwt = jwt.encode(
        payload, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM
    )

    await redis_client.set(
        f"refresh:{user_id}:{session_id}",
        jti,
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    # Update the family TTL (extend the family's lifetime)
    family_key = f"family:{user_id}:{family_id}"
    await redis_client.expire(family_key, config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60)

    return str(encoded_jwt)


def create_verification_token(data: dict[str, Any]) -> str:
    """
    Create a new JWT verification token

    Args:
        data: Dictionary containing token data (must include 'email' key)

    Returns:
        str: Encoded JWT verification token
    """
    expire = get_utc_now() + timedelta(
        minutes=config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES
    )

    payload: JWTPayload = {
        "sub": data.get("email", ""),  # email as a subject identifier
        "exp": int(expire.timestamp()),
        "mode": "verification_token",
    }

    token_data = {**data, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM
    )

    return str(encoded_jwt)


def create_reset_password_token(data: dict[str, Any]) -> str:
    """
    Create a new JWT password-reset token

    Args:
        data: Dictionary containing token data (must include 'email' key)

    Returns:
        str: Encoded JWT password reset token
    """
    expire = get_utc_now() + timedelta(
        minutes=config.jwt.RESET_PASSWORD_TOKEN_EXPIRE_MINUTES
    )

    payload: JWTPayload = {
        "sub": data.get("email", ""),  # email as a subject identifier
        "exp": int(expire.timestamp()),
        "mode": "reset_password_token",
    }

    token_data = {**data, **payload}

    encoded_jwt = jwt.encode(
        token_data, config.jwt.JWT_RESET_PASSWORD_SECRET_KEY, config.jwt.ALGORITHM
    )

    return str(encoded_jwt)
