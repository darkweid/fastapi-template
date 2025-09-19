from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt

from src.user.auth.dependencies import JWTPayload

from src.core.redis.client import redis_client
from src.core.utils.datetime_utils import get_utc_now
from src.main.config import config


async def create_access_token(data: dict[str, Any]) -> str:
    """
    Create a new JWT access token

    Args:
        data: Dictionary containing token data (must include 'sub' key with user ID)

    Returns:
        str: Encoded JWT access token
    """
    jti = str(uuid4())
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


async def create_refresh_token(data: dict[str, Any]) -> str:
    """
    Create a new JWT refresh token

    Args:
        data: Dictionary containing token data (must include 'sub' key with user ID)

    Returns:
        str: Encoded JWT refresh token
    """
    jti = str(uuid4())
    session_id = str(uuid4())
    expire = get_utc_now() + timedelta(minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES)

    payload: JWTPayload = {
        "sub": data["sub"],
        "exp": int(expire.timestamp()),
        "mode": "refresh_token",
        "jti": jti,
        "session_id": session_id,
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


async def invalidate_all_user_sessions(user_id: str) -> None:
    """
    Invalidates all sessions for a given user
    """
    access_pattern = f"access:{user_id}:*"
    refresh_pattern = f"refresh:{user_id}:*"

    access_keys = await redis_client.keys(access_pattern)
    refresh_keys = await redis_client.keys(refresh_pattern)

    if access_keys:
        await redis_client.delete(*access_keys)
    if refresh_keys:
        await redis_client.delete(*refresh_keys)
