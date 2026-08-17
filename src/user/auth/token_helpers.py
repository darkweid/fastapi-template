"""
Helper functions for token operations.

This module contains utility functions for working with JWT tokens,
including validation and token invalidation.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from src.core.errors.exceptions import UnauthorizedException
from src.core.utils.security import normalize_email
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import OneTimeTokenPurpose, auth_redis_keys
from src.user.auth.redis_scripts import ROTATE_REFRESH_TOKEN_SCRIPT


async def invalidate_all_user_sessions(user_id: str, redis_client: Redis) -> None:
    """
    Invalidates all sessions for a given user by walking the sessions:{uid}
    index - one SMEMBERS and one DEL instead of a keyspace SCAN, whose cost
    grows with the whole database rather than with this user's sessions.

    used:* markers are deliberately left to their TTL: the refresh keys are
    gone after the wipe, so a replayed rotated-out token cannot rotate anyway.

    Args:
        user_id: The user ID whose sessions should be invalidated
    """
    index_key = auth_redis_keys.sessions(user_id)
    session_ids = await redis_client.smembers(index_key)

    keys_to_delete: list[str] = [index_key]
    for raw_session_id in session_ids:
        session_id = (
            raw_session_id.decode("utf-8")
            if isinstance(raw_session_id, bytes)
            else raw_session_id
        )
        keys_to_delete.append(auth_redis_keys.access(user_id, session_id))
        keys_to_delete.append(auth_redis_keys.refresh(user_id, session_id))

    await redis_client.delete(*keys_to_delete)


async def invalidate_user_session(
    user_id: str,
    session_id: str,
    redis_client: Redis,
) -> None:
    """
    Invalidates a single user session by deleting its active auth keys and
    removing it from the sessions:{uid} index.

    Args:
        user_id: The user ID whose session should be invalidated.
        session_id: The session identifier to invalidate.
        redis_client: Redis client used to delete the active token keys.
    """
    await redis_client.delete(
        auth_redis_keys.access(user_id, session_id),
        auth_redis_keys.refresh(user_id, session_id),
    )
    await redis_client.srem(auth_redis_keys.sessions(user_id), session_id)


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


async def validate_token_structure(
    payload: JWTPayload, redis_client: Redis
) -> tuple[str, str, str]:
    """
    Validates that a token payload has all required fields.

    Args:
        payload: The JWT payload to validate

    Returns:
        tuple: A tuple containing user_id, session_id, and jti

    Raises:
        UnauthorizedException: If the token structure is invalid
    """
    user_id = payload.get("sub")
    session_id = payload.get("session_id")
    jti = payload.get("jti")

    if not user_id or not session_id or not jti:
        if user_id:
            await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Invalid token structure")

    return user_id, session_id, jti


async def execute_token_rotation(
    user_id: str,
    session_id: str,
    jti: str,
    redis_client: Redis,
) -> str:
    """
    Executes the atomic token rotation operation using a Lua script.

    Args:
        user_id: The user ID from the token
        session_id: The session ID from the token
        jti: The JTI (JWT ID) from the token

    Returns:
        str: The result of the token rotation operation ('OK', 'REUSED', or 'INVALID')

    Raises:
        UnauthorizedException: If the token has been reused or is invalid
    """

    refresh_ttl_seconds = config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    # The used marker lives exactly as long as a refresh token can: shorter and
    # a late replay reads INVALID instead of REUSED, longer buys nothing. There
    # is no separate knob because no other value is correct.
    used_ttl_seconds = refresh_ttl_seconds

    old_refresh_key = auth_redis_keys.refresh(user_id, session_id)
    used_refresh_key = auth_redis_keys.used(user_id, jti)

    result: str = await cast(
        Awaitable[str],
        redis_client.eval(
            ROTATE_REFRESH_TOKEN_SCRIPT,
            2,  # Number of keys
            old_refresh_key,
            used_refresh_key,
            jti,
            str(used_ttl_seconds),
        ),
    )

    if result == "REUSED":
        # Token reuse detected!
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Token reuse detected. All sessions invalidated.")
    elif result == "INVALID":
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Token invalidated or expired")

    return result
