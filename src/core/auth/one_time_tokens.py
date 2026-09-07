from typing import Any

import jwt
from redis.asyncio import Redis

from src.core.auth.challenges import INVALID_CHALLENGE_MESSAGE, ActiveChallengeRegistry
from src.core.auth.realm import AuthRealm
from src.core.auth.tokens import issue_token
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config

IDENTIFIER_CLAIM = "identifier"


async def issue_one_time_token(
    *,
    realm: AuthRealm,
    purpose: str,
    identifier: str,
    mode: str,
    ttl_minutes: int,
    redis_client: Redis,
    extra_data: dict[str, Any] | None = None,
) -> str:
    """
    Mint a single-use token and make it the realm's live challenge for its purpose.

    The identifier arrives normalized: the core cannot know whether it is an
    email, a phone number or a login, so it neither lowercases nor validates it.

    Returns:
        str: the encoded token, ready to be embedded in a link.
    """
    token, jti = await issue_token(
        sub=identifier,
        mode=mode,
        ttl_minutes=ttl_minutes,
        secret=realm.one_time_secret(purpose),
        redis_client=redis_client,
        keys=None,
        extra_data={**(extra_data or {}), IDENTIFIER_CLAIM: identifier},
    )

    await ActiveChallengeRegistry(realm).store(
        purpose, identifier, jti, ttl_minutes * 60, redis_client
    )
    return token


async def decode_one_time_token(
    token: str,
    *,
    realm: AuthRealm,
    purpose: str,
    redis_client: Redis,
    expected_mode: str | None = None,
) -> str:
    """
    Decode a single-use token and confirm it is still the live challenge.

    Callers keep their own jwt.ExpiredSignatureError / jwt.InvalidTokenError
    ladders: this raises only for what can be decided after a successful
    decode - a missing identifier, a mode mismatch, or a superseded challenge.

    Returns:
        str: the identifier the token was issued for.
    """
    payload = jwt.decode(
        token,
        realm.one_time_secret(purpose),
        algorithms=[config.jwt.ALGORITHM],
    )

    if expected_mode is not None and payload.get("mode") != expected_mode:
        raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

    identifier = payload.get(IDENTIFIER_CLAIM)
    if not identifier:
        raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

    await ActiveChallengeRegistry(realm).validate(
        purpose, identifier, payload.get("jti"), redis_client
    )
    return str(identifier)
