from unittest.mock import AsyncMock

import pytest

from src.core.errors.exceptions import InstanceProcessingException
from src.core.utils.security import build_email_throttle_key
from src.user.auth.services.verification_notifier import VerificationNotifier
from src.user.auth.tasks import send_verification_email_task
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


@pytest.mark.asyncio
async def test_verification_notifier_queues_task_with_expected_payload(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    notifier = VerificationNotifier(dispatcher=dispatcher, redis_client=fake_redis)
    user = build_user(email="user@example.com")

    await notifier.send_verification(uow=fake_uow, user=user)

    assert dispatcher.enqueue_transactional.await_args.args[:2] == (
        fake_uow,
        send_verification_email_task,
    )
    assert dispatcher.enqueue_transactional.await_args.args[2:] == (
        user.email,
        user.full_name,
    )
    # throttle_key travels as a keyword: a positional slot is one silent
    # mismatch away from binding the wrong value.
    assert dispatcher.enqueue_transactional.await_args.kwargs == {"throttle_key": None}


@pytest.mark.asyncio
async def test_verification_notifier_rejects_throttled_requests(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    notifier = VerificationNotifier(dispatcher=AsyncMock(), redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("resend_verification", user.email)
    await fake_redis.set(throttle_key, "1", ex=60)

    with pytest.raises(InstanceProcessingException):
        await notifier.send_verification(
            uow=fake_uow,
            user=user,
            throttle_key=throttle_key,
        )


@pytest.mark.asyncio
async def test_verification_notifier_cleans_throttle_key_when_queueing_fails(
    fake_redis: InMemoryRedis,
    fake_uow: FakeUnitOfWork,
) -> None:
    dispatcher = AsyncMock()
    dispatcher.enqueue_transactional.side_effect = RuntimeError("outbox insert failed")
    notifier = VerificationNotifier(dispatcher=dispatcher, redis_client=fake_redis)
    user = build_user(email="user@example.com")
    throttle_key = build_email_throttle_key("resend_verification", user.email)

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await notifier.send_verification(
            uow=fake_uow,
            user=user,
            throttle_key=throttle_key,
        )

    assert await fake_redis.exists(throttle_key) == 0


@pytest.mark.asyncio
async def test_release_throttle_deletes_key(fake_redis: InMemoryRedis) -> None:
    notifier = VerificationNotifier(dispatcher=AsyncMock(), redis_client=fake_redis)
    throttle_key = build_email_throttle_key("resend_verification", "user@example.com")
    await fake_redis.set(throttle_key, "1", ex=60)

    await notifier.release_throttle(throttle_key)

    assert await fake_redis.exists(throttle_key) == 0


@pytest.mark.asyncio
async def test_release_throttle_swallows_redis_errors() -> None:
    redis_client = AsyncMock()
    redis_client.delete.side_effect = ConnectionError("redis down")
    notifier = VerificationNotifier(dispatcher=AsyncMock(), redis_client=redis_client)

    await notifier.release_throttle("any-key")

    redis_client.delete.assert_awaited_once_with("any-key")
