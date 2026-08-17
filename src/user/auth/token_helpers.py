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
    index - one ZRANGE, one DEL of the token keys and one ZREM instead of a
    keyspace SCAN, whose cost grows with the whole database rather than with
    this user's sessions.

    used:* markers are deliberately left to their TTL: the refresh keys are
    gone after the wipe, so a replayed rotated-out token cannot rotate anyway.

    Args:
        user_id: The user ID whose sessions should be invalidated
    """
    index_key = auth_redis_keys.sessions(user_id)
    # The shared client decodes responses, so members arrive as str; the cast
    # narrows redis-py's union return type.
    session_ids = cast(list[str], await redis_client.zrange(index_key, 0, -1))

    token_keys: list[str] = []
    for session_id in session_ids:
        token_keys.append(auth_redis_keys.access(user_id, session_id))
        token_keys.append(auth_redis_keys.refresh(user_id, session_id))

    if token_keys:
        await redis_client.delete(*token_keys)
    if session_ids:
        # ZREM exactly what was read, never DEL of the whole key: a session
        # registered while this wipe runs must not vanish from the index
        # while its token keys survive.
        await redis_client.zrem(index_key, *session_ids)


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
    await redis_client.zrem(auth_redis_keys.sessions(user_id), session_id)


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


async def is_within_reuse_grace(user_id: str, jti: str, redis_client: Redis) -> bool:
    """A used marker younger than the grace window marks a benign double-submit."""
    grace = config.jwt.REFRESH_TOKEN_REUSE_GRACE_SECONDS
    if grace <= 0:
        return False

    used_at = await redis_client.get(auth_redis_keys.used(user_id, jti))
    if used_at is None:
        return False

    try:
        used_at_number = int(used_at)
    except (TypeError, ValueError):
        return False

    # The Redis server clock - the same one the rotation script stamped the
    # marker with, so the comparison needs no clock sync between app instances.
    seconds, _ = await redis_client.time()
    return (int(seconds) - used_at_number) <= grace


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
        str: The result of the token rotation operation ('OK' - every other
        script answer raises)

    Raises:
        UnauthorizedException: If the token has been reused or is invalid.
        A replay within the grace window answers the same generic 401 as an
        invalid token but leaves the session family intact.
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
            str(config.jwt.REFRESH_TOKEN_REUSE_GRACE_SECONDS),
        ),
    )

    if result == "GRACE":
        # A double-submit inside the grace window: reject the request but do
        # not treat it as theft - the family wipe would log out a user whose
        # client merely retried a refresh over a flaky connection.
        raise UnauthorizedException("Token invalidated or expired")
    if result == "REUSED":
        # Token reuse detected!
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Token reuse detected. All sessions invalidated.")
    if result == "INVALID":
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Token invalidated or expired")

    return result
