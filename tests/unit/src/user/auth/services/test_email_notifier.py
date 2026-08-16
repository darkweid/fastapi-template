from unittest.mock import AsyncMock

import pytest

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils.security import build_email_throttle_key
from src.user.auth.services.email_notifier import EmailNotifier
from src.user.auth.tasks import (
    send_reset_password_email_task,
    send_verification_email_task,
)
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis

NOTIFIER_CONFIGS = [
    pytest.param(
        send_verification_email_task,
        "We've already sent you a verification email.",
        "verification",
        "resend_verification",
        id="verification",
    ),
    pytest.param(
        send_reset_password_email_task,
        "We've already sent you a reset-password email.",
        "password reset",
        "password-reset",
        id="reset_password",
    ),
]


def build_notifier(
    dispatcher: AsyncMock,
    redis_client: InMemoryRedis,
    task: object,
    throttle_message: str,
    log_label: str,
) -> EmailNotifier:
    return EmailNotifier(
        dispatcher=dispatcher,
        redis_client=redis_client,
        task=task,  # type: ignore[arg-type]
        throttle_message=throttle_message,
        log_label=log_label,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "throttle_message", "log_label", "throttle_namespace"), NOTIFIER_CONFIGS
)
async def test_email_notifier_queues_task_with_expected_payload(
    task: object,
    throttle_message: str,
    log_label: str,
    throttle_namespace: str,
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    notifier = build_notifier(dispatcher, fake_redis, task, throttle_message, log_label)
    user = build_user(email="user@example.com")

    await notifier.send(uow=fake_uow, user=user)

    assert dispatcher.enqueue_transactional.await_args.args[:2] == (fake_uow, task)
    assert dispatcher.enqueue_transactional.await_args.args[2:] == (
        user.email,
        user.full_name,
    )
    # throttle_key travels as a keyword: a positional slot is one silent
    # mismatch away from binding the wrong value.
    assert dispatcher.enqueue_transactional.await_args.kwargs == {"throttle_key": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "throttle_message", "log_label", "throttle_namespace"), NOTIFIER_CONFIGS
)
async def test_email_notifier_rejects_throttled_requests(
    task: object,
    throttle_message: str,
    log_label: str,
    throttle_namespace: str,
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    notifier = build_notifier(
        AsyncMock(), fake_redis, task, throttle_message, log_label
    )
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key(throttle_namespace, user.email)
    await fake_redis.set(throttle_key, "1", ex=60)

    with pytest.raises(InstanceProcessingException, match=throttle_message):
        await notifier.send(uow=fake_uow, user=user, throttle_key=throttle_key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "throttle_message", "log_label", "throttle_namespace"), NOTIFIER_CONFIGS
)
async def test_email_notifier_cleans_throttle_key_when_queueing_fails(
    task: object,
    throttle_message: str,
    log_label: str,
    throttle_namespace: str,
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    dispatcher.enqueue_transactional.side_effect = RuntimeError("outbox insert failed")
    notifier = build_notifier(dispatcher, fake_redis, task, throttle_message, log_label)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key(throttle_namespace, user.email)

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await notifier.send(uow=fake_uow, user=user, throttle_key=throttle_key)

    assert await fake_redis.exists(throttle_key) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "throttle_message", "log_label", "throttle_namespace"), NOTIFIER_CONFIGS
)
async def test_release_throttle_deletes_key(
    task: object,
    throttle_message: str,
    log_label: str,
    throttle_namespace: str,
    fake_redis: InMemoryRedis,
) -> None:
    notifier = build_notifier(
        AsyncMock(), fake_redis, task, throttle_message, log_label
    )
    throttle_key = build_email_throttle_key(throttle_namespace, "user@example.com")
    await fake_redis.set(throttle_key, "1", ex=60)

    await notifier.release_throttle(throttle_key)

    assert await fake_redis.exists(throttle_key) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task", "throttle_message", "log_label", "throttle_namespace"), NOTIFIER_CONFIGS
)
async def test_release_throttle_swallows_redis_errors(
    task: object,
    throttle_message: str,
    log_label: str,
    throttle_namespace: str,
) -> None:
    redis_client = AsyncMock()
    redis_client.delete.side_effect = ConnectionError("redis down")
    notifier = build_notifier(
        AsyncMock(), redis_client, task, throttle_message, log_label
    )

    await notifier.release_throttle("any-key")

    redis_client.delete.assert_awaited_once_with("any-key")
