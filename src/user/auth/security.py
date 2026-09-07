from typing import Any

import jwt
from redis.asyncio import Redis

from src.core.auth.redis_keys import OneTimeTokenPurpose, auth_redis_keys
from src.core.auth.tokens import issue_token
from src.core.errors.exceptions import UnauthorizedException
from src.core.utils.security import normalize_email
from src.main.config import config


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
    ttl_minutes = config.jwt.VERIFICATION_TOKEN_EXPIRE_MINUTES

    token, jti = await issue_token(
        sub=email,
        mode="verification_token",
        ttl_minutes=ttl_minutes,
        secret=config.jwt.JWT_VERIFY_SECRET_KEY,
        redis_client=redis_client,
        extra_data={**data, "email": email},
    )

    await store_active_one_time_token(
        purpose="verification",
        email=email,
        jti=jti,
        ttl_seconds=ttl_minutes * 60,
        redis_client=redis_client,
    )

    return token


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
    ttl_minutes = config.jwt.RESET_PASSWORD_TOKEN_EXPIRE_MINUTES

    token, jti = await issue_token(
        sub=email,
        mode="reset_password_token",
        ttl_minutes=ttl_minutes,
        secret=config.jwt.JWT_RESET_PASSWORD_SECRET_KEY,
        redis_client=redis_client,
        extra_data={**data, "email": email},
    )

    await store_active_one_time_token(
        purpose="reset_password",
        email=email,
        jti=jti,
        ttl_seconds=ttl_minutes * 60,
        redis_client=redis_client,
    )

    return token


async def decode_one_time_token(
    token: str,
    *,
    secret: str,
    purpose: OneTimeTokenPurpose,
    redis_client: Redis,
    expected_mode: str | None = None,
) -> str:
    """
    Decode a one-time JWT (verification/reset-password) and confirm its jti
    is still the active one for the purpose.

    Callers keep their own jwt.ExpiredSignatureError/jwt.InvalidTokenError
    ladders: this only raises UnauthorizedException for what it can
    determine after a successful decode (missing email, mode mismatch, or an
    inactive/reused jti).

    Returns:
        str: the normalized email the token was issued for.
    """
    payload = jwt.decode(token, secret, algorithms=[config.jwt.ALGORITHM])

    if expected_mode is not None and payload.get("mode") != expected_mode:
        raise UnauthorizedException("Invalid or expired token.")

    email = payload.get("email")
    if not email:
        raise UnauthorizedException("Invalid or expired token.")

    normalized_email = normalize_email(email)
    await validate_active_one_time_token(
        purpose=purpose,
        email=normalized_email,
        jti=payload.get("jti"),
        redis_client=redis_client,
    )
    return normalized_email


async def store_active_one_time_token(
    purpose: OneTimeTokenPurpose,
    email: str,
    jti: str,
    ttl_seconds: int,
    redis_client: Redis,
) -> None:
    """
    Stores the active JTI for a single-use token identified by purpose and email.
    """
    normalized_email = normalize_email(email)
    await redis_client.set(
        auth_redis_keys.one_time_token(purpose, normalized_email),
        jti,
        ex=ttl_seconds,
    )


async def validate_active_one_time_token(
    purpose: OneTimeTokenPurpose,
    email: str,
    jti: str | None,
    redis_client: Redis,
) -> None:
    """
    Ensures the provided JTI matches the current active single-use token in Redis.
    """
    if not jti:
        raise UnauthorizedException("Invalid or expired token.")

    normalized_email = normalize_email(email)
    active_jti = await redis_client.get(
        auth_redis_keys.one_time_token(purpose, normalized_email)
    )

    if active_jti != jti:
        raise UnauthorizedException("Invalid or expired token.")


async def invalidate_active_one_time_token(
    purpose: OneTimeTokenPurpose,
    email: str,
    redis_client: Redis,
) -> None:
    """
    Deletes the active single-use token entry for the provided purpose and email.
    """
    normalized_email = normalize_email(email)
    await redis_client.delete(auth_redis_keys.one_time_token(purpose, normalized_email))
