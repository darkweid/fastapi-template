from collections.abc import Generator
from unittest.mock import AsyncMock

import pytest

from src.user.auth.tasks import send_verification_email_task
from taskiq_worker.broker import broker
from taskiq_worker.dependencies import get_tasks_redis_client
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


@pytest.fixture
def override_tasks_redis_client(fake_redis: InMemoryRedis) -> Generator[None]:
    """Point the shared broker's redis provider at the fake client for one test.

    The broker singleton is imported by every test module, so the override
    must be removed afterwards or it leaks into unrelated tests.
    """
    broker.dependency_overrides[get_tasks_redis_client] = ProvideAsyncValue(fake_redis)
    yield
    del broker.dependency_overrides[get_tasks_redis_client]


@pytest.mark.asyncio
async def test_kiq_resolves_redis_client_through_broker_dependency_injection(
    fake_redis: InMemoryRedis,
    override_tasks_redis_client: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given: get_tasks_redis_client is overridden on the broker for a fake client.
    When: send_verification_email_task is queued through .kiq(), not called directly.
    Then: the task body receives the fake client via TaskiqDepends and sends the email.
    """
    mock_mailer = MockMailer()
    monkeypatch.setattr("src.user.auth.tasks.get_mailer", ProvideValue(mock_mailer))

    await send_verification_email_task.kiq(
        "user@example.com",
        "John Doe",
        throttle_key=None,
    )

    assert len(mock_mailer.sent_template_emails) == 1
    assert mock_mailer.sent_template_emails[0]["recipients"] == ["user@example.com"]


@pytest.mark.asyncio
async def test_get_tasks_redis_client_closes_client_after_normal_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = AsyncMock()
    monkeypatch.setattr(
        "taskiq_worker.dependencies.create_redis_client", lambda _dsn: fake_client
    )

    generator = get_tasks_redis_client()
    client = await generator.__anext__()
    assert client is fake_client

    with pytest.raises(StopAsyncIteration):
        await generator.__anext__()

    fake_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_tasks_redis_client_closes_client_when_consumer_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = AsyncMock()
    monkeypatch.setattr(
        "taskiq_worker.dependencies.create_redis_client", lambda _dsn: fake_client
    )

    generator = get_tasks_redis_client()
    await generator.__anext__()

    with pytest.raises(RuntimeError, match="consumer failed"):
        await generator.athrow(RuntimeError("consumer failed"))

    fake_client.aclose.assert_awaited_once()
