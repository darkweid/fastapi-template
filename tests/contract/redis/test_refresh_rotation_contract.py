import asyncio
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import jwt
import pytest
from redis.asyncio import Redis

from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.session_issuance import issue_session_pair
from src.core.auth.tokens import rotate_session_tokens
from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import TokenModel
from src.main.config import config
from src.user.auth.realm import USER_AUTH_REALM

KEYS = USER_AUTH_REALM.keys


def _claims(token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    return decoded


async def _login(redis_client: Redis, subject_id: str) -> JWTPayload:
    pair = await issue_session_pair(
        realm=USER_AUTH_REALM,
        subject_id=subject_id,
        claims={},
        redis_client=redis_client,
    )
    return cast(JWTPayload, _claims(pair.refresh_token))


def _session_keys(subject_id: str, session_id: str) -> list[str]:
    return [
        KEYS.access(subject_id, session_id),
        KEYS.refresh(subject_id, session_id),
        KEYS.latest_access(subject_id, session_id),
    ]


async def test_rotation_registers_the_new_pair_as_the_live_session(
    redis_backend: Redis,
) -> None:
    """Logout and the next refresh trust exactly these keys; a pair the script
    did not register is a session the user cannot use or end."""
    subject_id = str(uuid4())
    old = await _login(redis_backend, subject_id)
    session_id = old["session_id"]

    rotated = await rotate_session_tokens(old, redis_backend, realm=USER_AUTH_REALM)

    new_refresh = _claims(rotated.refresh_token)
    new_access = _claims(rotated.access_token)
    assert (
        await redis_backend.get(KEYS.refresh(subject_id, session_id))
        == new_refresh["jti"]
    )
    assert (
        await redis_backend.get(KEYS.access(subject_id, session_id))
        == new_access["jti"]
    )
    assert (
        await redis_backend.get(KEYS.latest_access(subject_id, session_id))
        == new_access["jti"]
    )
    now_seconds, _ = await redis_backend.time()
    score = await redis_backend.zscore(KEYS.sessions(subject_id), session_id)
    assert score is not None and score > now_seconds
    refresh_ttl = await redis_backend.ttl(KEYS.refresh(subject_id, session_id))
    assert 0 < refresh_ttl <= config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60


async def test_second_use_inside_the_grace_window_keeps_the_session(
    redis_backend: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client retrying a refresh over a flaky connection is not a thief; wiping
    its sessions would log it out for a network blip."""
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    subject_id = str(uuid4())
    old = await _login(redis_backend, subject_id)
    rotated = await rotate_session_tokens(old, redis_backend, realm=USER_AUTH_REALM)

    with pytest.raises(UnauthorizedException, match="invalidated or expired"):
        await rotate_session_tokens(old, redis_backend, realm=USER_AUTH_REALM)

    assert (
        await redis_backend.get(KEYS.refresh(subject_id, old["session_id"]))
        == _claims(rotated.refresh_token)["jti"]
    )


async def test_reuse_after_the_grace_window_wipes_every_session(
    redis_backend: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rotated-out token presented again means it leaked; every session of the
    subject goes, not only the one the token belonged to."""
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    subject_id = str(uuid4())
    first = await _login(redis_backend, subject_id)
    second = await _login(redis_backend, subject_id)
    await rotate_session_tokens(first, redis_backend, realm=USER_AUTH_REALM)

    with pytest.raises(UnauthorizedException, match="reuse detected"):
        await rotate_session_tokens(first, redis_backend, realm=USER_AUTH_REALM)

    for session_id in (first["session_id"], second["session_id"]):
        assert await redis_backend.exists(*_session_keys(subject_id, session_id)) == 0
    assert await redis_backend.zrange(KEYS.sessions(subject_id), 0, -1) == []


async def test_a_jti_the_session_never_held_is_invalid_and_wipes(
    redis_backend: Redis,
) -> None:
    """A refresh token whose jti is not the session's live one was never minted
    for it; rotation must neither accept it nor leave the session usable."""
    subject_id = str(uuid4())
    old = await _login(redis_backend, subject_id)
    forged = cast(JWTPayload, {**old, "jti": str(uuid4())})

    with pytest.raises(UnauthorizedException, match="invalidated or expired"):
        await rotate_session_tokens(forged, redis_backend, realm=USER_AUTH_REALM)

    assert (
        await redis_backend.exists(*_session_keys(subject_id, old["session_id"])) == 0
    )


async def test_concurrent_rotations_of_one_token_let_exactly_one_through(
    real_redis: Redis,
    redis_client_factory: Callable[[], Redis],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tabs refreshing at once must not both come back with a live pair:
    that is the race the rotation script exists to close."""
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    subject_id = str(uuid4())
    old = await _login(real_redis, subject_id)

    results = await asyncio.gather(
        *(
            rotate_session_tokens(old, redis_client_factory(), realm=USER_AUTH_REALM)
            for _ in range(8)
        ),
        return_exceptions=True,
    )

    winners = [result for result in results if isinstance(result, TokenModel)]
    assert len(winners) == 1
    assert all(
        isinstance(result, UnauthorizedException)
        for result in results
        if not isinstance(result, TokenModel)
    )
    assert (
        await real_redis.get(KEYS.refresh(subject_id, old["session_id"]))
        == _claims(winners[0].refresh_token)["jti"]
    )
