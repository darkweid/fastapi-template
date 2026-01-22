"""
Helper functions for token operations.

This module contains utility functions for working with JWT tokens,
including validation, family checking, and token invalidation.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_scripts import ROTATE_REFRESH_TOKEN_SCRIPT


async def invalidate_all_user_sessions(user_id: str, redis_client: Redis) -> None:
    """
    Invalidates all sessions for a given user by deleting all related Redis keys.
    Uses SCAN for non-blocking key discovery.

    Args:
        user_id: The user ID whose sessions should be invalidated
    """
    patterns = [
        f"access:{user_id}:*",
        f"refresh:{user_id}:*",
        f"family:{user_id}:*",
        f"used:{user_id}:*",  # Also clean up used tokens
    ]

    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor, match=pattern, count=100
            )
            if keys:
                await redis_client.delete(*keys)
            if cursor == 0:
                break


async def validate_token_family(
    user_id: str, family_id: str | None, redis_client: Redis
) -> None:
    """
    Validates that a token belongs to an active token family.

    Args:
        user_id: The user ID from the token
        family_id: The family ID from the token

    Raises:
        UnauthorizedException: If the family doesn't exist or the token structure is invalid
    """
    if not family_id:
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Invalid token structure")

    family_key = f"family:{user_id}:{family_id}"
    family_exists = await redis_client.exists(family_key)

    if not family_exists:
        # Family doesn't exist - possible token reuse attempt
        await invalidate_all_user_sessions(user_id, redis_client)
        raise UnauthorizedException("Token has been invalidated due to potential reuse")

    return None


async def validate_token_structure(
    payload: JWTPayload, redis_client: Redis
) -> tuple[str, str, str, str]:
    """
    Validates that a token payload has all required fields.

    Args:
        payload: The JWT payload to validate

    Returns:
        tuple: A tuple containing user_id, session_id, jti, and family_id

    Raises:
        UnauthorizedException: If the token structure is invalid
    """
    try:
        user_id = payload["sub"]
        session_id = payload["session_id"]
        jti = payload.get("jti")
        family_id = payload.get("family")

        if not jti or not family_id:
            await invalidate_all_user_sessions(user_id, redis_client)
            raise UnauthorizedException("Invalid token structure")

        return user_id, session_id, jti, family_id
    except KeyError:
        raise UnauthorizedException("Invalid token structure")


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
    used_ttl_seconds = min(
        config.jwt.REFRESH_TOKEN_USED_TTL_SECONDS,
        refresh_ttl_seconds,
    )

    old_refresh_key = f"refresh:{user_id}:{session_id}"
    used_refresh_key = f"used:{user_id}:{jti}"

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
