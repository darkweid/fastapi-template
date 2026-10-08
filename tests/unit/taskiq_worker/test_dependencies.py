from collections.abc import Generator
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest

from src.main.config import config
from src.user.auth.tasks import send_verification_email_task
from taskiq_worker.broker import broker
import taskiq_worker.dependencies as tasks_dependencies
from taskiq_worker.dependencies import close_tasks_redis_client, get_tasks_redis_client
from tests.fakes.email import MockMailer
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


@pytest.fixture(autouse=True)
def _reset_tasks_redis_singleton() -> Generator[None]:
    """The worker Redis client is a module-level singleton; tests must not leak it."""
    tasks_dependencies._tasks_redis_client = None
    tasks_dependencies._cache_redis_client = None
    yield
    tasks_dependencies._tasks_redis_client = None
    tasks_dependencies._cache_redis_client = None


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
async def test_get_tasks_redis_client_reuses_same_client_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = AsyncMock()
    monkeypatch.setattr(
        "taskiq_worker.dependencies.create_redis_client", lambda _dsn: fake_client
    )

    first = await get_tasks_redis_client().__anext__()
    second = await get_tasks_redis_client().__anext__()

    assert first is fake_client
    assert second is fake_client
    fake_client.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_close_tasks_redis_client_closes_and_clears_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = AsyncMock()
    monkeypatch.setattr(
        "taskiq_worker.dependencies.create_redis_client", lambda _dsn: fake_client
    )
    await get_tasks_redis_client().__anext__()

    await close_tasks_redis_client()

    fake_client.aclose.assert_awaited_once()
    assert tasks_dependencies._tasks_redis_client is None


@pytest.mark.asyncio
async def test_close_tasks_redis_client_is_a_noop_without_a_client() -> None:
    await close_tasks_redis_client()

    assert tasks_dependencies._tasks_redis_client is None


async def test_cache_client_is_the_tasks_client_when_no_cache_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.cache, "CACHE_REDIS_URL", SecretStr(""))
    tasks_client = InMemoryRedis()
    monkeypatch.setattr(
        tasks_dependencies, "get_tasks_redis_singleton", lambda: tasks_client
    )

    assert tasks_dependencies.get_cache_redis_singleton() is tasks_client

    # Closing the cache client must not close the tasks client it borrowed.
    await tasks_dependencies.close_cache_redis_client()
    assert not tasks_client.closed


async def test_cache_client_is_built_once_from_the_cache_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_with: list[str] = []
    dedicated = InMemoryRedis()

    def create_redis_client(connection_url: str) -> InMemoryRedis:
        created_with.append(connection_url)
        return dedicated

    monkeypatch.setattr(
        config.cache, "CACHE_REDIS_URL", SecretStr("redis://cache.internal:6379/0")
    )
    monkeypatch.setattr(tasks_dependencies, "create_redis_client", create_redis_client)

    first = tasks_dependencies.get_cache_redis_singleton()
    second = tasks_dependencies.get_cache_redis_singleton()
    await tasks_dependencies.close_cache_redis_client()

    assert first is second is dedicated
    assert created_with == ["redis://cache.internal:6379/0"]
    assert dedicated.closed
    assert tasks_dependencies._cache_redis_client is None


def test_disabled_cache_builds_no_dedicated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_with: list[str] = []
    tasks_client = InMemoryRedis()
    monkeypatch.setattr(config.cache, "CACHE_ENABLED", False)
    monkeypatch.setattr(
        config.cache, "CACHE_REDIS_URL", SecretStr("redis://cache.internal:6379/0")
    )
    monkeypatch.setattr(tasks_dependencies, "create_redis_client", created_with.append)
    monkeypatch.setattr(
        tasks_dependencies, "get_tasks_redis_singleton", lambda: tasks_client
    )

    assert tasks_dependencies.get_cache_redis_singleton() is tasks_client
    assert created_with == []
