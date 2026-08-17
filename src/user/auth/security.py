from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

import jwt
from redis.asyncio import Redis

from src.core.errors.exceptions import UnauthorizedException
from src.core.utils.datetime_utils import get_utc_now
from src.core.utils.security import normalize_email
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import OneTimeTokenPurpose, auth_redis_keys
from src.user.auth.token_helpers import (
    execute_token_rotation,
    store_active_one_time_token,
    validate_active_one_time_token,
    validate_token_structure,
)


async def _issue_token(
    *,
    sub: str,
    mode: Literal[
        "access_token", "refresh_token", "verification_token", "reset_password_token"
    ],
    ttl_minutes: int,
    secret: str,
    redis_client: Redis,
    session_id: str | None = None,
    redis_key: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Build, sign and (optionally) register one JWT's jti in Redis.

    Every token issuer shares this shape; only the TTL setting, mode, subject
    and signing secret vary. `redis_key` covers the access/refresh case,
    where the jti is registered directly under a session key. Callers that
    track their active token differently (the one-time verification/reset
    tokens, via `store_active_one_time_token`) leave it None and register the
    jti themselves using the returned value.
    """
    jti = str(uuid4())
    expire = get_utc_now() + timedelta(minutes=ttl_minutes)

    payload: JWTPayload = {
        "sub": sub,
        "exp": int(expire.timestamp()),
        "mode": mode,
        "jti": jti,
    }
    if session_id is not None:
        payload["session_id"] = session_id

    token_data: dict[str, Any] = {**(extra_data or {}), **payload}
    encoded_jwt = jwt.encode(token_data, secret, config.jwt.ALGORITHM)

    if redis_key is not None:
        await redis_client.set(redis_key, jti, ex=ttl_minutes * 60)

    if redis_key is not None and session_id is not None:
        # Register the session in the per-user index so a wipe can find every
        # session without a keyspace SCAN. Members are scored by the moment
        # their refresh lifetime ends and stale ones are pruned here, on
        # issuance - nothing else removes a session whose refresh token
        # silently expired, so without the prune the index would only grow.
        # The index TTL always covers the refresh lifetime - the
        # longest-lived credential of any session.
        index_key = auth_redis_keys.sessions(sub)
        index_ttl_seconds = config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
        now_seconds, _ = await redis_client.time()
        await redis_client.zremrangebyscore(index_key, 0, int(now_seconds))
        await redis_client.zadd(
            index_key, {session_id: int(now_seconds) + index_ttl_seconds}
        )
        await redis_client.expire(index_key, index_ttl_seconds)

    return str(encoded_jwt), jti


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
    if session_id is None:
        session_id = str(uuid4())

    token, _ = await _issue_token(
        sub=data["sub"],
        mode="access_token",
        ttl_minutes=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES,
        secret=config.jwt.JWT_USER_SECRET_KEY,
        redis_client=redis_client,
        session_id=session_id,
        redis_key=auth_redis_keys.access(data["sub"], session_id),
    )
    return token


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
    if session_id is None:
        session_id = str(uuid4())

    token, _ = await _issue_token(
        sub=data["sub"],
        mode="refresh_token",
        ttl_minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        secret=config.jwt.JWT_USER_SECRET_KEY,
        redis_client=redis_client,
        session_id=session_id,
        redis_key=auth_redis_keys.refresh(data["sub"], session_id),
    )
    return token


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

    user_id, old_session_id, old_jti = await validate_token_structure(
        old_payload, redis_client
    )

    await execute_token_rotation(user_id, old_session_id, old_jti, redis_client)

    token, _ = await _issue_token(
        sub=user_id,
        mode="refresh_token",
        ttl_minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        secret=config.jwt.JWT_USER_SECRET_KEY,
        redis_client=redis_client,
        session_id=old_session_id,
        redis_key=auth_redis_keys.refresh(user_id, old_session_id),
    )
    return token


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

    token, jti = await _issue_token(
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

    token, jti = await _issue_token(
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
