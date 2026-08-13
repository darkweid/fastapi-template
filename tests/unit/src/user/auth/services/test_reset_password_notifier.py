from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import URL

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils.security import build_email_throttle_key
from src.user.auth.services.reset_password_notifier import ResetPasswordNotifier
from src.user.auth.tasks import send_reset_password_email_task
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


@pytest.mark.asyncio
async def test_reset_password_notifier_queues_task_with_expected_payload(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    notifier = ResetPasswordNotifier(dispatcher=dispatcher, redis_client=fake_redis)
    user = build_user(email="user@example.com")

    await notifier.send_password_reset_email(
        uow=fake_uow,
        user=user,
        base_url=URL("http://testserver/"),
    )

    assert dispatcher.enqueue_transactional.await_args.args[:2] == (
        fake_uow,
        send_reset_password_email_task,
    )
    assert dispatcher.enqueue_transactional.await_args.args[2:] == (
        user.email,
        user.full_name,
        "http://testserver/",
        "v1/users/auth/password/reset/confirm",
        None,
    )


@pytest.mark.asyncio
async def test_reset_password_notifier_rejects_throttled_requests(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    notifier = ResetPasswordNotifier(dispatcher=AsyncMock(), redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("password-reset", user.email)
    await fake_redis.set(throttle_key, "1", ex=60)

    with pytest.raises(InstanceProcessingException):
        await notifier.send_password_reset_email(
            uow=fake_uow,
            user=user,
            base_url=URL("http://testserver/"),
            throttle_key=throttle_key,
        )


@pytest.mark.asyncio
async def test_reset_password_notifier_cleans_throttle_key_when_queueing_fails(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    dispatcher.enqueue_transactional.side_effect = RuntimeError("outbox insert failed")
    notifier = ResetPasswordNotifier(dispatcher=dispatcher, redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("password-reset", user.email)

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await notifier.send_password_reset_email(
            uow=fake_uow,
            user=user,
            base_url=URL("http://testserver/"),
            throttle_key=throttle_key,
        )

    assert await fake_redis.exists(throttle_key) == 0
