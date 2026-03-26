from unittest.mock import AsyncMock, MagicMock

import pytest

from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.tasks import (
    _send_reset_password_email,
    _send_verification_email,
    send_reset_password_email_task,
    send_verification_email_task,
)
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue


def test_send_verification_email_task_runs_async_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_coroutine = None

    def fake_execute_coroutine_sync(*, coroutine):
        nonlocal captured_coroutine
        captured_coroutine = coroutine
        coroutine.close()

    execute_mock = MagicMock(side_effect=fake_execute_coroutine_sync)
    monkeypatch.setattr("src.user.auth.tasks.execute_coroutine_sync", execute_mock)

    send_verification_email_task(
        "user@example.com",
        "John Doe",
        "http://testserver/",
        "v1/users/auth/verify",
        "throttle:key",
    )

    execute_mock.assert_called_once()


@pytest.mark.asyncio
async def test_send_verification_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        "src.user.auth.tasks.create_redis_client",
        lambda *args, **kwargs: fake_redis,
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await _send_verification_email(
        email="user@example.com",
        full_name="John Doe",
        base_url="http://testserver/",
        verify_path="v1/users/auth/verify",
        throttle_key=None,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "verification.html"
    assert "v1/users/auth/verify?token=" in sent_email["template_data"]["link"]
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", "user@example.com")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_send_verification_email_cleans_up_token_and_throttle_on_failure(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        mock_mailer,
        "send_template",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )
    monkeypatch.setattr(
        "src.user.auth.tasks.create_redis_client",
        lambda *args, **kwargs: fake_redis,
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(RuntimeError, match="send failed"):
        await _send_verification_email(
            email="user@example.com",
            full_name="John Doe",
            base_url="http://testserver/",
            verify_path="v1/users/auth/verify",
            throttle_key="throttle:key",
        )

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0


def test_send_reset_password_email_task_runs_async_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_execute_coroutine_sync(*, coroutine):
        coroutine.close()

    execute_mock = MagicMock(side_effect=fake_execute_coroutine_sync)
    monkeypatch.setattr("src.user.auth.tasks.execute_coroutine_sync", execute_mock)

    send_reset_password_email_task(
        "user@example.com",
        "John Doe",
        "http://testserver/",
        "v1/users/auth/password/reset/confirm",
        "throttle:key",
    )

    execute_mock.assert_called_once()


@pytest.mark.asyncio
async def test_send_reset_password_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        "src.user.auth.tasks.create_redis_client",
        lambda *args, **kwargs: fake_redis,
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await _send_reset_password_email(
        email="user@example.com",
        full_name="John Doe",
        base_url="http://testserver/",
        reset_link_path="v1/users/auth/password/reset/confirm",
        throttle_key=None,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "reset_password.html"
    assert "password/reset/confirm?token=" in sent_email["template_data"]["link"]
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", "user@example.com")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_send_reset_password_email_cleans_up_token_and_throttle_on_failure(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr(
        mock_mailer,
        "send_template",
        AsyncMock(side_effect=RuntimeError("send failed")),
    )
    monkeypatch.setattr(
        "src.user.auth.tasks.create_redis_client",
        lambda *args, **kwargs: fake_redis,
    )
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(RuntimeError, match="send failed"):
        await _send_reset_password_email(
            email="user@example.com",
            full_name="John Doe",
            base_url="http://testserver/",
            reset_link_path="v1/users/auth/password/reset/confirm",
            throttle_key="throttle:key",
        )

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0
