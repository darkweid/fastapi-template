from collections.abc import Iterator
import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest
from taskiq import TaskiqState
from taskiq.schedule_sources import LabelScheduleSource

from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import get_cache_instance, reset_cache
from src.main.config import config
import taskiq_worker.app as worker_app
import taskiq_worker.dependencies as tasks_dependencies
from taskiq_worker.scheduler import scheduler
from tests.fakes.redis import InMemoryRedis

EXPECTED_TASKS = {
    "cleanup_unverified_users",
    "send_verification_email",
    "send_reset_password_email",
    "send_email",
    "send_email_with_s3_attachments",
    "outbox_sweeper",
    "outbox_purge",
}

_LIST_REGISTERED_TASKS_SCRIPT = (
    "import json\n"
    "from taskiq_worker.app import broker\n"
    "print(json.dumps(list(broker.get_all_tasks())))\n"
)


def test_app_registers_every_task() -> None:
    """`taskiq_worker.app`'s own registration imports must register every task.

    This runs in a fresh subprocess that imports only `taskiq_worker.app`. In
    the full suite, other test modules import the task modules at collection
    time and populate the shared `broker` singleton before this test would
    run, so an in-process assertion here would pass even if `app.py` were
    missing a registration import - it would silently stop registering tasks
    for the real worker CLI target without any test catching it.
    """
    result = subprocess.run(
        [sys.executable, "-c", _LIST_REGISTERED_TASKS_SCRIPT],
        capture_output=True,
        text=True,
        env={**os.environ, "TESTING": "true"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    # loggers.get_logger writes to stdout by design (containers own log
    # shipping), so any import-time log line lands ahead of the JSON payload;
    # the payload is always the script's last printed line.
    json_line = result.stdout.strip().splitlines()[-1]
    registered_tasks = set(json.loads(json_line))
    assert EXPECTED_TASKS <= registered_tasks


def test_scheduler_reads_labels_from_broker() -> None:
    assert any(isinstance(source, LabelScheduleSource) for source in scheduler.sources)


@pytest.fixture
def clean_cache_singleton() -> Iterator[None]:
    reset_cache()
    yield
    reset_cache()


async def test_worker_startup_wires_the_shared_cache(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = InMemoryRedis()
    monkeypatch.setattr(worker_app, "get_cache_redis_singleton", lambda: fake_redis)

    await worker_app.on_worker_startup(TaskiqState())

    cache = get_cache_instance()
    assert isinstance(cache, RedisCache)
    # Same prefix as the API's on_cache_startup - that is what makes the keys
    # shared between the two processes.
    assert cache._prefix == config.cache.CACHE_KEY_PREFIX  # noqa: SLF001


async def test_worker_shutdown_resets_the_cache_singleton(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = InMemoryRedis()
    monkeypatch.setattr(worker_app, "get_cache_redis_singleton", lambda: fake_redis)
    await worker_app.on_worker_startup(TaskiqState())

    await worker_app.on_worker_shutdown(TaskiqState())

    with pytest.raises(RuntimeError, match="Cache is not initialized"):
        get_cache_instance()


async def test_worker_shutdown_closes_every_http_client(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider session left open at shutdown leaks its sockets and logs
    "Unclosed client session" on every worker restart."""
    closed: list[bool] = []

    async def close_http_clients() -> None:
        closed.append(True)

    fake_redis = InMemoryRedis()
    monkeypatch.setattr(worker_app, "get_cache_redis_singleton", lambda: fake_redis)
    monkeypatch.setattr(worker_app, "close_http_clients", close_http_clients)
    await worker_app.on_worker_startup(TaskiqState())

    await worker_app.on_worker_shutdown(TaskiqState())

    assert closed == [True]


async def test_worker_shutdown_closes_redis_when_an_http_client_fails_to_close(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising close must not leave the worker's Redis pool open behind it."""
    redis_closed: list[bool] = []

    async def close_http_clients() -> None:
        raise RuntimeError("provider session refused to close")

    async def close_tasks_redis_client() -> None:
        redis_closed.append(True)

    monkeypatch.setattr(worker_app, "close_http_clients", close_http_clients)
    monkeypatch.setattr(
        worker_app, "close_tasks_redis_client", close_tasks_redis_client
    )

    with pytest.raises(RuntimeError, match="refused to close"):
        await worker_app.on_worker_shutdown(TaskiqState())

    assert redis_closed == [True]


@pytest.fixture
def dedicated_cache_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryRedis]:
    dedicated = InMemoryRedis()
    dedicated.ping = AsyncMock(return_value=True)  # type: ignore[method-assign]
    monkeypatch.setattr(
        config.cache, "CACHE_REDIS_URL", SecretStr("redis://cache.internal:6379/0")
    )
    monkeypatch.setattr(
        tasks_dependencies, "create_redis_client", lambda connection_url: dedicated
    )
    tasks_dependencies._cache_redis_client = None
    yield dedicated
    tasks_dependencies._cache_redis_client = None


async def test_worker_cache_runs_on_the_pinged_dedicated_client(
    clean_cache_singleton: None, dedicated_cache_client: InMemoryRedis
) -> None:
    """A worker caching on the main instance while the API caches elsewhere would
    drop every task-side invalidation, and one on a wrong URL would only find out
    at its first cache call."""
    await worker_app.on_worker_startup(TaskiqState())

    assert get_cache_instance()._redis is dedicated_cache_client  # noqa: SLF001
    dedicated_cache_client.ping.assert_awaited_once()  # type: ignore[attr-defined]


async def test_worker_startup_fails_when_the_cache_instance_does_not_answer(
    clean_cache_singleton: None, dedicated_cache_client: InMemoryRedis
) -> None:
    dedicated_cache_client.ping.return_value = False  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="ping failed"):
        await worker_app.on_worker_startup(TaskiqState())


async def test_disabled_worker_cache_pings_no_dedicated_client(
    clean_cache_singleton: None,
    dedicated_cache_client: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.cache, "CACHE_ENABLED", False)
    monkeypatch.setattr(
        tasks_dependencies, "get_tasks_redis_singleton", lambda: InMemoryRedis()
    )

    await worker_app.on_worker_startup(TaskiqState())

    dedicated_cache_client.ping.assert_not_awaited()  # type: ignore[attr-defined]
    assert get_cache_instance()._redis is not dedicated_cache_client  # noqa: SLF001


async def test_worker_shutdown_closes_the_cache_client(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache client closes even when another close raises, and before the
    tasks client it may be borrowing."""
    closed: list[str] = []

    async def close_cache_redis_client() -> None:
        closed.append("cache")

    async def close_tasks_redis_client() -> None:
        closed.append("tasks")

    async def close_http_clients() -> None:
        raise RuntimeError("provider session refused to close")

    monkeypatch.setattr(
        worker_app, "close_cache_redis_client", close_cache_redis_client
    )
    monkeypatch.setattr(
        worker_app, "close_tasks_redis_client", close_tasks_redis_client
    )
    monkeypatch.setattr(worker_app, "close_http_clients", close_http_clients)

    with pytest.raises(RuntimeError, match="refused to close"):
        await worker_app.on_worker_shutdown(TaskiqState())

    assert closed == ["cache", "tasks"]


async def test_worker_shutdown_closes_the_retry_schedule_source(
    clean_cache_singleton: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SmartRetryMiddleware writes every retry through this source's own pool, so
    a worker that skipped it would leave those connections open, even when
    another close raises."""
    retry_source = AsyncMock()

    async def close_http_clients() -> None:
        raise RuntimeError("provider session refused to close")

    monkeypatch.setattr(worker_app, "retry_schedule_source", retry_source)
    monkeypatch.setattr(worker_app, "close_http_clients", close_http_clients)

    with pytest.raises(RuntimeError, match="refused to close"):
        await worker_app.on_worker_shutdown(TaskiqState())

    retry_source.shutdown.assert_awaited_once()
