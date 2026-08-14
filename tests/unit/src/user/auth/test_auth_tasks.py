from unittest.mock import AsyncMock

import pytest

from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.tasks import (
    send_reset_password_email_task,
    send_verification_email_task,
)
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue


@pytest.mark.asyncio
async def test_send_verification_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await send_verification_email_task(
        "user@example.com",
        "John Doe",
        throttle_key=None,
        redis_client=fake_redis,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "verification.html"
    assert sent_email["template_data"]["link"].startswith(
        "http://frontend.test/verify-email?token="
    )
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
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(RuntimeError, match="send failed"):
        await send_verification_email_task(
            "user@example.com",
            "John Doe",
            throttle_key="throttle:key",
            redis_client=fake_redis,
        )

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0


@pytest.mark.asyncio
async def test_send_reset_password_email_creates_token_and_sends_email(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await send_reset_password_email_task(
        "user@example.com",
        "John Doe",
        throttle_key=None,
        redis_client=fake_redis,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    sent_email = mock_mailer.sent_template_emails[0]
    assert sent_email["recipients"] == ["user@example.com"]
    assert sent_email["template_name"] == "reset_password.html"
    assert sent_email["template_data"]["link"].startswith(
        "http://frontend.test/reset-password?token="
    )
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
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))
    await fake_redis.set("throttle:key", "1", ex=60)

    with pytest.raises(RuntimeError, match="send failed"):
        await send_reset_password_email_task(
            "user@example.com",
            "John Doe",
            throttle_key="throttle:key",
            redis_client=fake_redis,
        )

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", "user@example.com")
        )
        == 0
    )
    assert await fake_redis.exists("throttle:key") == 0


def test_auth_task_registration() -> None:
    assert send_verification_email_task.task_name == "send_verification_email"
    assert send_verification_email_task.labels["retry_on_error"] is True
    assert send_reset_password_email_task.task_name == "send_reset_password_email"
    assert send_reset_password_email_task.labels["retry_on_error"] is True
