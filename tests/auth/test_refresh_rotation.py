from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.user.auth.security import rotate_refresh_token
import src.user.auth.token_helpers as token_helpers


def _base_payload() -> dict[str, str | int]:
    return {
        "sub": "user-id",
        "session_id": "old-session",
        "jti": "old-jti",
        "family": "family-id",
        "exp": 9999999999,
        "mode": "refresh_token",
    }


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_USED_TTL_SECONDS", 100)
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(config.jwt, "JWT_USER_SECRET_KEY", "secret")
    monkeypatch.setattr(config.jwt, "ALGORITHM", "HS256")


@pytest.fixture()
def redis_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    mock.exists.return_value = True
    mock.eval.return_value = "OK"
    mock.set.return_value = None
    mock.expire.return_value = None
    return mock


@pytest.mark.asyncio
async def test_rotate_refresh_token_success(redis_mock: AsyncMock) -> None:
    payload = _base_payload()

    token = await rotate_refresh_token(payload, redis_mock)
    decoded = jwt.decode(
        token, config.jwt.JWT_USER_SECRET_KEY, algorithms=[config.jwt.ALGORITHM]
    )

    assert decoded["family"] == payload["family"]
    assert decoded["session_id"] != payload["session_id"]
    assert decoded["jti"] != payload["jti"]

    used_ttl = redis_mock.eval.await_args.args[5]
    assert used_ttl == str(min(config.jwt.REFRESH_TOKEN_USED_TTL_SECONDS, 600))
    redis_mock.set.assert_awaited()
    redis_mock.expire.assert_awaited_with(
        f"family:{payload['sub']}:{payload['family']}",
        config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )


@pytest.mark.asyncio
async def test_rotate_refresh_token_reuse_detected(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.eval.return_value = "REUSED"
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with(payload["sub"], redis_mock)


@pytest.mark.asyncio
async def test_rotate_refresh_token_invalid_state(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.eval.return_value = "INVALID"
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with(payload["sub"], redis_mock)


@pytest.mark.asyncio
async def test_rotate_refresh_token_missing_family_invalidates(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("family")

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with(payload["sub"], redis_mock)
    redis_mock.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_refresh_token_missing_jti_invalidates(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("jti")

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with(payload["sub"], redis_mock)
    redis_mock.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_refresh_token_family_not_found(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.exists.return_value = False
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with(payload["sub"], redis_mock)
    redis_mock.eval.assert_not_awaited()
