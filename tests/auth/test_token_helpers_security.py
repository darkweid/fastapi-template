from datetime import datetime, timezone
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.user.auth.jwt_payload_schema import JWTPayload
import src.user.auth.security as security
import src.user.auth.token_helpers as token_helpers


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.config.jwt, "JWT_USER_SECRET_KEY", "secret")
    monkeypatch.setattr(security.config.jwt, "JWT_VERIFY_SECRET_KEY", "verify")
    monkeypatch.setattr(security.config.jwt, "JWT_RESET_PASSWORD_SECRET_KEY", "reset")
    monkeypatch.setattr(security.config.jwt, "ALGORITHM", "HS256")
    monkeypatch.setattr(security.config.jwt, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(security.config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(security.config.jwt, "REFRESH_TOKEN_USED_TTL_SECONDS", 100)
    monkeypatch.setattr(security.config.jwt, "VERIFICATION_TOKEN_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(security.config.jwt, "RESET_PASSWORD_TOKEN_EXPIRE_MINUTES", 5)


@pytest.fixture()
def redis_mock(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_create_access_token_stores_jti(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        security, "get_utc_now", lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
    )

    token = await security.create_access_token({"sub": "user"}, redis_client=redis_mock)
    decoded = jwt.decode(
        token, "secret", algorithms=["HS256"], options={"verify_exp": False}
    )

    assert decoded["sub"] == "user"
    assert decoded["mode"] == "access_token"
    redis_mock.set.assert_awaited()


@pytest.mark.asyncio
async def test_create_refresh_token_stores_family(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        security, "get_utc_now", lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
    )

    token = await security.create_refresh_token(
        {"sub": "user"}, redis_client=redis_mock
    )
    decoded = jwt.decode(
        token, "secret", algorithms=["HS256"], options={"verify_exp": False}
    )

    assert decoded["mode"] == "refresh_token"
    assert "family" in decoded
    assert redis_mock.set.await_count == 2


@pytest.mark.asyncio
async def test_validate_token_structure_missing_fields(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)
    payload: JWTPayload = {"sub": "u1", "session_id": "s1", "mode": "refresh_token"}

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_structure(payload, redis_mock)

    invalidate_mock.assert_awaited_once_with("u1", redis_mock)


@pytest.mark.asyncio
async def test_validate_token_family_missing_family(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_family("u1", None, redis_mock)

    invalidate_mock.assert_awaited_once_with("u1", redis_mock)


@pytest.mark.asyncio
async def test_validate_token_family_not_exists(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.exists.return_value = False
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_family("u1", "family", redis_mock)

    invalidate_mock.assert_awaited_once_with("u1", redis_mock)


@pytest.mark.asyncio
async def test_execute_token_rotation_reused(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.eval.return_value = "REUSED"
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.execute_token_rotation("u1", "s1", "j1", redis_mock)

    invalidate_mock.assert_awaited_once_with("u1", redis_mock)


@pytest.mark.asyncio
async def test_execute_token_rotation_invalid(
    redis_mock: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis_mock.eval.return_value = "INVALID"
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.execute_token_rotation("u1", "s1", "j1", redis_mock)

    invalidate_mock.assert_awaited_once_with("u1", redis_mock)


@pytest.mark.asyncio
async def test_execute_token_rotation_ok(redis_mock: AsyncMock) -> None:
    redis_mock.eval.return_value = "OK"

    result = await token_helpers.execute_token_rotation("u1", "s1", "j1", redis_mock)

    assert result == "OK"
    redis_mock.eval.assert_awaited()


@pytest.mark.asyncio
async def test_invalidate_all_user_sessions(redis_mock: AsyncMock) -> None:
    redis_mock.keys.side_effect = [
        ["access:1:a1"],
        ["refresh:1:r1"],
        ["family:1:f1"],
        ["used:1:u1"],
    ]

    await token_helpers.invalidate_all_user_sessions("1", redis_mock)

    assert redis_mock.delete.await_count == 4
