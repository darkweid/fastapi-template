from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import URL

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils.security import build_email_throttle_key
from src.user.auth.services.reset_password_notifier import ResetPasswordNotifier
from src.user.auth.tasks import send_reset_password_email_task
from tests.factories.user_factory import build_user
from tests.fakes.redis import InMemoryRedis


@pytest.mark.asyncio
async def test_reset_password_notifier_queues_task_with_expected_payload(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kiq_mock = AsyncMock()
    monkeypatch.setattr(send_reset_password_email_task, "kiq", kiq_mock)
    notifier = ResetPasswordNotifier(redis_client=fake_redis)
    user = build_user(email="user@example.com")

    await notifier.send_password_reset_email(
        user=user,
        base_url=URL("http://testserver/"),
    )

    kiq_mock.assert_awaited_once_with(
        user.email,
        user.full_name,
        "http://testserver/",
        "v1/users/auth/password/reset/confirm",
        None,
    )


@pytest.mark.asyncio
async def test_reset_password_notifier_rejects_throttled_requests(
    fake_redis: InMemoryRedis,
) -> None:
    notifier = ResetPasswordNotifier(redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("password-reset", user.email)
    await fake_redis.set(throttle_key, "1", ex=60)

    with pytest.raises(InstanceProcessingException):
        await notifier.send_password_reset_email(
            user=user,
            base_url=URL("http://testserver/"),
            throttle_key=throttle_key,
        )


@pytest.mark.asyncio
async def test_reset_password_notifier_cleans_throttle_key_when_queueing_fails(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kiq_mock = AsyncMock(side_effect=RuntimeError("kiq failed"))
    monkeypatch.setattr(send_reset_password_email_task, "kiq", kiq_mock)
    notifier = ResetPasswordNotifier(redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("password-reset", user.email)

    with pytest.raises(RuntimeError, match="kiq failed"):
        await notifier.send_password_reset_email(
            user=user,
            base_url=URL("http://testserver/"),
            throttle_key=throttle_key,
        )

    assert await fake_redis.exists(throttle_key) == 0
