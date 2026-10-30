"""
Redis-side session bookkeeping shared by every realm: what invalidating a
session removes, and what rotating a refresh token does atomically.
"""

from collections.abc import Awaitable
import json
from typing import NamedTuple, cast

from redis.asyncio import Redis

from loggers import get_logger
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.redis_keys import AuthRedisKeyBuilder
from src.core.auth.redis_scripts import ROTATE_REFRESH_TOKEN_SCRIPT
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config

logger = get_logger(__name__)

REUSE_DETECTED_MESSAGE = "Token reuse detected. Session invalidated."


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
        token_keys.append(keys.latest_access(subject_id, session_id))

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
        keys.latest_access(subject_id, session_id),
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


class SessionPair(NamedTuple):
    """The jtis, issue instant and expiry instants (unix seconds) of one
    access/refresh pair.

    Everything a token's signature covers besides the subject, the session and
    the mode, so the same pair can be signed again byte for byte.
    """

    issued_at: int
    refresh_jti: str
    refresh_expires_at: int
    access_jti: str
    access_expires_at: int


class UsedMarker(NamedTuple):
    """What the rotation script stamped on a rotated-out refresh token."""

    rotated_at: int
    successor: SessionPair


def parse_used_marker(value: str) -> UsedMarker | None:
    """Read a used marker the rotation script wrote; None for any other shape.

    A marker that cannot be read is never forgiven: callers treat None as reuse.
    """
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            return None
        refresh_jti = raw["refresh_jti"]
        access_jti = raw["access_jti"]
        if not isinstance(refresh_jti, str) or not isinstance(access_jti, str):
            return None
        return UsedMarker(
            rotated_at=int(raw["at"]),
            successor=SessionPair(
                issued_at=int(raw["issued_at"]),
                refresh_jti=refresh_jti,
                refresh_expires_at=int(raw["refresh_expires_at"]),
                access_jti=access_jti,
                access_expires_at=int(raw["access_expires_at"]),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def is_replay_of_live_pair(
    used_marker: str,
    subject_id: str,
    session_id: str,
    redis_client: Redis,
    *,
    keys: AuthRedisKeyBuilder,
) -> bool:
    """Whether a rotated-out refresh token may still be answered with its pair.

    True only inside the grace window and while the pair its rotation handed
    out is still the session's live one: the client never received that pair.
    The rotation script applies the same rule atomically; this read is a
    pre-check, so a token that fails it is refused before any rotation runs.
    """
    grace = config.jwt.REFRESH_TOKEN_REUSE_GRACE_SECONDS
    marker = parse_used_marker(used_marker)
    if grace <= 0 or marker is None:
        return False

    # The Redis server clock - the same one the rotation script stamped the
    # marker with, so the comparison needs no clock sync between app instances.
    seconds, _ = await redis_client.time()
    if int(seconds) - marker.rotated_at > grace:
        return False
    live_refresh_jti = await redis_client.get(keys.refresh(subject_id, session_id))
    return bool(live_refresh_jti == marker.successor.refresh_jti)


async def execute_token_rotation(
    subject_id: str,
    session_id: str,
    jti: str,
    redis_client: Redis,
    *,
    keys: AuthRedisKeyBuilder,
    successor: SessionPair,
) -> SessionPair:
    """
    Run the rotation script and answer the pair the session now holds.

    On 'OK' that is `successor`, which the script has already registered as
    the session's live pair (the access jti also as the one logout accepts)
    together with its index entry, in the same unit as the check. On 'REPLAY'
    the token was rotated moments ago and its successor refresh token has not
    been rotated yet, so the
    answer is that earlier pair: the caller signs it again and nothing changes
    in Redis.

    Every other verdict ends the token's session - only that one: a stolen
    refresh token reaches no other session of the subject, and ending them all
    would let anyone holding an old token sign the subject out everywhere.
    All of them raise 401 under one error code, but the message a detected
    reuse carries differs from the rest and reaches the client.
    """

    refresh_ttl_seconds = config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    # The used marker lives exactly as long as a refresh token can: shorter and
    # a late replay of a stolen token is no longer recognised as reuse - it
    # reads INVALID, which says nothing about theft - longer buys nothing.
    # There is no separate knob because no other value is correct.
    used_ttl_seconds = refresh_ttl_seconds

    result: str | list[str] = await cast(
        Awaitable[str | list[str]],
        redis_client.eval(
            ROTATE_REFRESH_TOKEN_SCRIPT,
            5,  # Number of keys
            keys.refresh(subject_id, session_id),
            keys.used(subject_id, jti),
            keys.access(subject_id, session_id),
            keys.sessions(subject_id),
            keys.latest_access(subject_id, session_id),
            jti,
            str(used_ttl_seconds),
            str(config.jwt.REFRESH_TOKEN_REUSE_GRACE_SECONDS),
            session_id,
            successor.refresh_jti,
            str(refresh_ttl_seconds),
            successor.access_jti,
            str(config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
            str(successor.refresh_expires_at),
            str(successor.access_expires_at),
            str(successor.issued_at),
        ),
    )

    if result == "OK":
        return successor

    if isinstance(result, list) and len(result) == 2 and result[0] == "REPLAY":
        marker = parse_used_marker(result[1])
        if marker is not None:
            # The one path that hands a session's live pair to whoever holds
            # its previous refresh token: worth a trace for a later inquiry.
            logger.info(
                "[RotateRefreshToken] Replayed rotation answered with its pair; "
                "subject=%s session=%s rotated_at=%s",
                subject_id,
                session_id,
                marker.rotated_at,
            )
            return marker.successor
        # The script only answers REPLAY over a marker it could read; one this
        # code cannot read falls through to the reuse branch below.

    # Everything that is not one of the script's verdicts lands here, and lands
    # on the side of ending the session. A verdict this code does not recognise
    # means the script, the client or whatever sits between them is not what
    # this function thinks it is, and rotation is the one place where guessing
    # "probably fine" hands out a session against a token nothing verified.
    if result != "REUSED" and result != "INVALID":
        logger.error(
            "[RotateRefreshToken] Unrecognised rotation verdict %r; "
            "treating it as a reuse and ending the session",
            result,
        )

    await invalidate_session(subject_id, session_id, redis_client, keys=keys)
    if result == "INVALID":
        raise UnauthorizedException("Token invalidated or expired")
    raise UnauthorizedException(REUSE_DETECTED_MESSAGE)
