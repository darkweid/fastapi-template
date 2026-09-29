from datetime import timedelta
from typing import Any
from uuid import uuid4

import jwt
from redis.asyncio import Redis

from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.realm import AuthRealm
from src.core.auth.redis_keys import AuthRedisKeyBuilder
from src.core.auth.token_helpers import execute_token_rotation, validate_token_structure
from src.core.schemas import TokenModel
from src.core.utils.datetime_utils import get_utc_now
from src.main.config import config


def build_token(
    *,
    sub: str,
    mode: str,
    ttl_minutes: int,
    secret: str,
    session_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Sign one JWT and return it with its jti. Nothing is registered in Redis:
    the token is not live until a caller stores the jti.
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
    return str(jwt.encode(token_data, secret, config.jwt.ALGORITHM)), jti


async def issue_token(
    *,
    sub: str,
    mode: str,
    ttl_minutes: int,
    secret: str,
    redis_client: Redis,
    keys: AuthRedisKeyBuilder | None = None,
    session_id: str | None = None,
    redis_key: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Build, sign and (optionally) register one JWT's jti in Redis.

    Every token issuer shares this shape; only the TTL setting, mode, subject
    and signing secret vary. `redis_key` covers the access/refresh case,
    where the jti is registered directly under a session key. Callers that
    track their active token differently leave it None and register the jti
    themselves using the returned value.
    """
    encoded_jwt, jti = build_token(
        sub=sub,
        mode=mode,
        ttl_minutes=ttl_minutes,
        secret=secret,
        session_id=session_id,
        extra_data=extra_data,
    )

    if redis_key is not None:
        await redis_client.set(redis_key, jti, ex=ttl_minutes * 60)

    if redis_key is not None and session_id is not None:
        if not keys:
            # A session registered under redis_key without an index entry
            # would be invisible to a mass wipe - silently, since the write
            # above already succeeded.
            raise ValueError(
                "keys is required to register a session (redis_key + session_id)"
            )
        # Register the session in the per-user index so a wipe can find every
        # session without a keyspace SCAN. Members are scored by the moment
        # their refresh lifetime ends and stale ones are pruned here, on
        # issuance - nothing else removes a session whose refresh token
        # silently expired, so without the prune the index would only grow.
        # The index TTL always covers the refresh lifetime - the
        # longest-lived credential of any session.
        index_key = keys.sessions(sub)
        index_ttl_seconds = config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
        now_seconds, _ = await redis_client.time()
        await redis_client.zremrangebyscore(index_key, 0, int(now_seconds))
        await redis_client.zadd(
            index_key, {session_id: int(now_seconds) + index_ttl_seconds}
        )
        await redis_client.expire(index_key, index_ttl_seconds)

    return encoded_jwt, jti


async def create_access_token(
    data: dict[str, Any],
    redis_client: Redis,
    *,
    realm: AuthRealm,
    session_id: str | None = None,
) -> str:
    """
    Issue an access token and register its jti under the session's Redis key.

    `data` must carry `sub`, which the annotation cannot say. Omitting
    `session_id` does not mean the current session - it opens a new one, which
    is what login wants and what a refresh must never do.
    """
    if session_id is None:
        session_id = str(uuid4())

    token, jti = await issue_token(
        sub=data["sub"],
        mode="access_token",
        ttl_minutes=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES,
        secret=realm.secret,
        redis_client=redis_client,
        keys=realm.keys,
        session_id=session_id,
        redis_key=realm.keys.access(data["sub"], session_id),
    )
    # As long as either token of the session can live: logout still names the
    # session by this access token after it expires.
    await redis_client.set(
        realm.keys.latest_access(data["sub"], session_id),
        jti,
        ex=max(
            config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES,
            config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        )
        * 60,
    )
    return token


async def create_refresh_token(
    data: dict[str, Any],
    redis_client: Redis,
    *,
    realm: AuthRealm,
    session_id: str | None = None,
) -> str:
    """
    Issue a refresh token and register its jti under the session's Redis key.

    Same contract as create_access_token: `sub` is required inside `data`, and
    an omitted `session_id` opens a new session rather than continuing one.
    """
    if session_id is None:
        session_id = str(uuid4())

    token, _ = await issue_token(
        sub=data["sub"],
        mode="refresh_token",
        ttl_minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        secret=realm.secret,
        redis_client=redis_client,
        keys=realm.keys,
        session_id=session_id,
        redis_key=realm.keys.refresh(data["sub"], session_id),
    )
    return token


async def rotate_session_tokens(
    old_payload: JWTPayload, redis_client: Redis, *, realm: AuthRealm
) -> TokenModel:
    """
    Burn the presented refresh token and hand out a new access/refresh pair for
    the same session.

    Presenting a token that was already rotated out is theft until proven
    otherwise: outside the short grace window it costs the subject every
    session, not just this request. The pair keeps the old session id, so a
    client that refreshes does not appear as a new login, and it is signed
    before the rotation script runs because the script registers both jtis in
    the same atomic step that consumes the old one.
    """

    subject_id, session_id, old_jti = await validate_token_structure(
        old_payload, redis_client, keys=realm.keys
    )

    refresh_token, refresh_jti = build_token(
        sub=subject_id,
        mode="refresh_token",
        ttl_minutes=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        secret=realm.secret,
        session_id=session_id,
    )
    access_token, access_jti = build_token(
        sub=subject_id,
        mode="access_token",
        ttl_minutes=config.jwt.ACCESS_TOKEN_EXPIRE_MINUTES,
        secret=realm.secret,
        session_id=session_id,
    )

    await execute_token_rotation(
        subject_id,
        session_id,
        old_jti,
        redis_client,
        keys=realm.keys,
        new_refresh_jti=refresh_jti,
        new_access_jti=access_jti,
    )
    return TokenModel(access_token=access_token, refresh_token=refresh_token)
