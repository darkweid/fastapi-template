"""
Redis-side session bookkeeping shared by every realm: what invalidating a
session removes, and what rotating a refresh token does atomically.
"""

from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.redis_keys import AuthRedisKeyBuilder
from src.core.auth.redis_scripts import ROTATE_REFRESH_TOKEN_SCRIPT
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config


async def invalidate_all_sessions(
    subject_id: str, redis_client: Redis, *, keys: AuthRedisKeyBuilder
) -> None:
    """
    Invalidates all sessions for a given subject by walking the sessions:{uid}
    index - one ZRANGE, one DEL of the token keys and one ZREM instead of a
    keyspace SCAN, whose cost grows with the whole database rather than with
    this subject's sessions.

    used:* markers are deliberately left to their TTL: the refresh keys are
    gone after the wipe, so a replayed rotated-out token cannot rotate anyway.
    """
    index_key = keys.sessions(subject_id)
    # The shared client decodes responses, so members arrive as str; the cast
    # narrows redis-py's union return type.
    session_ids = cast(list[str], await redis_client.zrange(index_key, 0, -1))

    token_keys: list[str] = []
    for session_id in session_ids:
        token_keys.append(keys.access(subject_id, session_id))
        token_keys.append(keys.refresh(subject_id, session_id))

    if token_keys:
        await redis_client.delete(*token_keys)
    if session_ids:
        # ZREM exactly what was read, never DEL of the whole key: a session
        # registered while this wipe runs must not vanish from the index
        # while its token keys survive.
        await redis_client.zrem(index_key, *session_ids)


async def invalidate_session(
    subject_id: str,
    session_id: str,
    redis_client: Redis,
    *,
    keys: AuthRedisKeyBuilder,
) -> None:
    """
    Drop one session: its active auth keys, and its entry in the
    sessions:{uid} index that invalidate_all_sessions walks.
    """
    await redis_client.delete(
        keys.access(subject_id, session_id),
        keys.refresh(subject_id, session_id),
    )
    await redis_client.zrem(keys.sessions(subject_id), session_id)


async def validate_token_structure(
    payload: JWTPayload, redis_client: Redis, *, keys: AuthRedisKeyBuilder
) -> tuple[str, str, str]:
    """
    Read the (subject, session, jti) triple a rotation needs.

    A payload that names a subject but is missing a session or a jti is not a
    token this system ever minted, so it costs that subject every session
    before the generic 401 - the shape can only come from tampering.
    """
    subject_id = payload.get("sub")
    session_id = payload.get("session_id")
    jti = payload.get("jti")

    if not subject_id or not session_id or not jti:
        if subject_id:
            await invalidate_all_sessions(subject_id, redis_client, keys=keys)
        raise UnauthorizedException("Invalid token structure")

    return subject_id, session_id, jti


async def is_within_reuse_grace(
    used_marker: str | bytes | None, redis_client: Redis
) -> bool:
    """A used marker younger than the grace window marks a benign double-submit.

    Takes the already-fetched marker value so the caller's existence check and
    this age check share one GET instead of an EXISTS/GET pair that could
    disagree between round-trips.
    """
    grace = config.jwt.REFRESH_TOKEN_REUSE_GRACE_SECONDS
    if grace <= 0 or used_marker is None:
        return False

    try:
        used_at_number = int(used_marker)
    except (TypeError, ValueError):
        return False

    # The Redis server clock - the same one the rotation script stamped the
    # marker with, so the comparison needs no clock sync between app instances.
    seconds, _ = await redis_client.time()
    return (int(seconds) - used_at_number) <= grace


async def execute_token_rotation(
    subject_id: str,
    session_id: str,
    jti: str,
    redis_client: Redis,
    *,
    keys: AuthRedisKeyBuilder,
) -> str:
    """
    Run the rotation script and translate its verdict.

    Answers 'OK' or raises; there is no other return. GRACE is a double-submit
    inside the reuse window and leaves the session family intact, while REUSED
    and INVALID wipe every session of the subject first. All three raise 401
    under one error code, but the message a detected reuse carries differs from
    the other two and reaches the client - only the code is uniform.
    """

    refresh_ttl_seconds = config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    # The used marker lives exactly as long as a refresh token can: shorter and
    # a late replay reads INVALID instead of REUSED, longer buys nothing. There
    # is no separate knob because no other value is correct.
    used_ttl_seconds = refresh_ttl_seconds

    old_refresh_key = keys.refresh(subject_id, session_id)
    used_refresh_key = keys.used(subject_id, jti)

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
        # not treat it as theft - the family wipe would log out a subject whose
        # client merely retried a refresh over a flaky connection.
        raise UnauthorizedException("Token invalidated or expired")
    if result == "REUSED":
        await invalidate_all_sessions(subject_id, redis_client, keys=keys)
        raise UnauthorizedException("Token reuse detected. All sessions invalidated.")
    if result == "INVALID":
        await invalidate_all_sessions(subject_id, redis_client, keys=keys)
        raise UnauthorizedException("Token invalidated or expired")

    return result
