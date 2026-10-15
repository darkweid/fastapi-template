import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.auth.credentials import verify_jti
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.session_issuance import issue_session_pair
import src.core.auth.token_helpers as token_helpers
from src.core.auth.tokens import rotate_session_tokens
from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import TokenModel
from src.main.config import config
from src.user.auth.realm import USER_AUTH_REALM
from tests.fakes.redis import InMemoryRedis

TEST_JWT_USER_SECRET_KEY = "test-jwt-user-secret-key-not-real"

AUTH_KEYS = USER_AUTH_REALM.keys

# Pinned wall clock for grace-window math; the fake's key expiry runs on
# time.monotonic(), so freezing this cannot make keys expire mid-test.
FROZEN_NOW = 1_755_000_000


def _base_payload() -> dict[str, str | int]:
    return {
        "sub": "user-id",
        "session_id": "old-session",
        "jti": "old-jti",
        "exp": 9999999999,
        "mode": "refresh_token",
    }


def _decode(token: str | None) -> Any:
    return jwt.decode(
        token, config.jwt.JWT_USER_SECRET_KEY, algorithms=[config.jwt.ALGORITHM]
    )


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(config.jwt, "JWT_USER_SECRET_KEY", TEST_JWT_USER_SECRET_KEY)
    monkeypatch.setattr(config.jwt, "ALGORITHM", "HS256")


@pytest.mark.asyncio
async def test_rotate_session_tokens_success(fake_redis: InMemoryRedis) -> None:
    """
    Given: old refresh key exists for an active refresh payload.
    When: refresh token rotation is executed.
    Then: a new token is issued, the session refresh key points to the new jti,
    and the used marker is created.
    """
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    tokens = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    decoded = jwt.decode(
        tokens.refresh_token,
        config.jwt.JWT_USER_SECRET_KEY,
        algorithms=[config.jwt.ALGORITHM],
    )

    assert decoded["session_id"] == payload["session_id"]
    assert decoded["jti"] != payload["jti"]

    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
    old_refresh_key = AUTH_KEYS.refresh(
        str(payload["sub"]),
        str(payload["session_id"]),
    )
    assert await fake_redis.exists(used_key) == 1
    assert await fake_redis.exists(old_refresh_key) == 1
    assert await fake_redis.get(old_refresh_key) == decoded["jti"]


@pytest.mark.asyncio
async def test_used_marker_ttl_tracks_the_refresh_token_lifetime(
    fake_redis: InMemoryRedis,
) -> None:
    # The marker must cover the rotated-out token's whole remaining lifetime:
    # shorter, and a replayed copy after marker expiry reads as INVALID instead
    # of REUSED; longer buys nothing because the token itself has expired.
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
    assert (
        await fake_redis.ttl(used_key) == config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    )


@pytest.mark.asyncio
async def test_rotation_stamps_the_instant_and_the_new_pair_on_the_used_marker(
    fake_redis: InMemoryRedis,
) -> None:
    # The grace check compares the instant against the Redis server clock, and a
    # replay inside the window is answered by signing this exact pair again.
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    tokens = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    refresh = _decode(tokens.refresh_token)
    access = _decode(tokens.access_token)
    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
    marker = await fake_redis.get(used_key)
    assert marker is not None
    assert json.loads(marker) == {
        "at": FROZEN_NOW,
        "refresh_jti": refresh["jti"],
        "refresh_expires_at": refresh["exp"],
        "access_jti": access["jti"],
        "access_expires_at": access["exp"],
    }


async def _rotate_once(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch, grace: int
) -> tuple[dict[str, str | int], TokenModel]:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", grace)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    rotated = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    return payload, rotated


@pytest.mark.asyncio
async def test_a_replay_inside_the_grace_window_gets_the_same_pair(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mobile client whose refresh response was lost retries with the token it
    still holds; a 401 there signs it out for a network blip, and a new pair
    would leave the first one live beside it."""
    payload, rotated = await _rotate_once(fake_redis, monkeypatch, grace=30)
    live_keys = {
        key: await fake_redis.get(key)
        for key in (
            AUTH_KEYS.refresh("user-id", "old-session"),
            AUTH_KEYS.access("user-id", "old-session"),
            AUTH_KEYS.latest_access("user-id", "old-session"),
        )
    }
    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 30)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_session", invalidate_mock)

    replayed = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    assert replayed == rotated
    for key, value in live_keys.items():
        assert await fake_redis.get(key) == value
    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_replay_after_the_pair_rotated_on_is_reuse(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the pair was used, whoever presents the token it replaced is not the
    client that lost an answer: inside the window too, that is reuse."""
    payload, rotated = await _rotate_once(fake_redis, monkeypatch, grace=30)
    await rotate_session_tokens(
        _decode(rotated.refresh_token), fake_redis, realm=USER_AUTH_REALM
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        "user-id", "old-session", fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_a_replay_after_a_wipe_does_not_bring_the_session_back(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A password change seconds after a refresh ends the session; the old
    token's marker must not re-sign a pair the wipe already revoked."""
    payload, _ = await _rotate_once(fake_redis, monkeypatch, grace=30)
    await token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "old-session")) == 0


@pytest.mark.asyncio
async def test_a_replay_after_the_grace_window_ends_only_its_session(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stolen refresh token reaches only its own session; ending every session
    would let anyone holding an old token sign the subject out everywhere."""
    payload, _ = await _rotate_once(fake_redis, monkeypatch, grace=30)
    await fake_redis.set(AUTH_KEYS.refresh("user-id", "other-session"), "other-jti")
    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 31)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "old-session")) == 0
    assert (
        await fake_redis.get(AUTH_KEYS.refresh("user-id", "other-session"))
        == "other-jti"
    )


@pytest.mark.asyncio
async def test_a_replay_does_not_extend_the_grace_window(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay writes nothing; if it restamped the marker, a thief could keep
    re-signing the owner's pair for as long as they kept replaying."""
    payload, rotated = await _rotate_once(fake_redis, monkeypatch, grace=30)
    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 10)
    assert (
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
        == rotated
    )
    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 31)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_a_replay_after_a_logout_is_refused_without_calling_it_reuse(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user ended this session; a retry of the token it rotated out must
    neither bring it back nor report a theft that did not happen."""
    payload, _ = await _rotate_once(fake_redis, monkeypatch, grace=30)
    await token_helpers.invalidate_session(
        "user-id", "old-session", fake_redis, keys=AUTH_KEYS
    )
    token = jwt.encode(payload, TEST_JWT_USER_SECRET_KEY, algorithm="HS256")

    with pytest.raises(UnauthorizedException, match="Token invalidated or expired"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "old-session")) == 0
    assert await fake_redis.exists(AUTH_KEYS.access("user-id", "old-session")) == 0


@pytest.mark.asyncio
async def test_zero_grace_disables_the_window(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, _ = await _rotate_once(fake_redis, monkeypatch, grace=0)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        "user-id", "old-session", fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_a_marker_without_a_readable_pair_is_reuse(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker that names no pair (corrupted, or written before markers carried
    one) has nothing to answer with, and is never forgiven by default."""
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await fake_redis.setex(
        AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"])),
        100,
        str(int(fake_redis.wall_clock())),
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        "user-id", "old-session", fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_rotate_session_tokens_invalid_state(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jti the session does not hold was never minted for it; the session is
    ended, and only that one."""
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        "wrong-jti",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="invalidated or expired"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        "user-id", "old-session", fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_rotate_session_tokens_missing_jti_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: refresh payload misses jti field.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for the user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("jti")

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


async def _live_session(fake_redis: InMemoryRedis) -> JWTPayload:
    tokens = await issue_session_pair(
        realm=USER_AUTH_REALM,
        subject_id="user-id",
        claims={},
        redis_client=fake_redis,
        session_id="session-1",
    )
    return jwt.decode(
        tokens.refresh_token,
        config.jwt.JWT_USER_SECRET_KEY,
        algorithms=[config.jwt.ALGORITHM],
    )


async def _assert_dead(token: str, fake_redis: InMemoryRedis) -> None:
    with pytest.raises(UnauthorizedException):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_a_wipe_right_after_rotation_kills_the_new_pair(
    fake_redis: InMemoryRedis,
) -> None:
    """A password change that lands just after a refresh must still end the
    session the refresh produced - the new pair is indexed before the wipe reads."""
    payload = await _live_session(fake_redis)

    rotated = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    await token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    await _assert_dead(rotated.access_token, fake_redis)
    await _assert_dead(rotated.refresh_token, fake_redis)


@pytest.mark.asyncio
async def test_rotation_after_a_wipe_is_refused_and_registers_nothing(
    fake_redis: InMemoryRedis,
) -> None:
    payload = await _live_session(fake_redis)
    await token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "session-1")) == 0
    assert await fake_redis.exists(AUTH_KEYS.access("user-id", "session-1")) == 0
    assert await fake_redis.zrange(AUTH_KEYS.sessions("user-id"), 0, -1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation_first", [True, False])
async def test_a_wipe_racing_a_rotation_leaves_no_live_session(
    fake_redis: InMemoryRedis, rotation_first: bool
) -> None:
    """The wipe spans several round-trips; whichever way the two interleave,
    nothing the rotation wrote may survive it."""
    payload = await _live_session(fake_redis)
    rotation = rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    wipe = token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    results = await asyncio.gather(
        *((rotation, wipe) if rotation_first else (wipe, rotation)),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, TokenModel):
            await _assert_dead(result.access_token, fake_redis)
            await _assert_dead(result.refresh_token, fake_redis)
    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "session-1")) == 0
    assert await fake_redis.exists(AUTH_KEYS.access("user-id", "session-1")) == 0
