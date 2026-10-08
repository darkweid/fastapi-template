from unittest.mock import AsyncMock

from taskiq import InMemoryBroker
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import ListRedisScheduleSource, RedisStreamBroker

from taskiq_worker.broker import (
    RetryScheduleSource,
    broker,
    create_production_broker,
    create_retry_schedule_source,
)
from taskiq_worker.middlewares import SentryMiddleware


def test_testing_env_selects_inmemory_broker() -> None:
    # The suite runs with TESTING=true, so the module-level broker must be the
    # inline in-memory one.
    assert isinstance(broker, InMemoryBroker)


def test_production_broker_assembly() -> None:
    production = create_production_broker()

    assert isinstance(production, RedisStreamBroker)
    # No result backend attached: the broker keeps taskiq's do-nothing default.
    assert type(production.result_backend).__name__ == "DummyResultBackend"
    retry_middleware = next(
        m for m in production.middlewares if isinstance(m, SmartRetryMiddleware)
    )
    assert isinstance(retry_middleware.schedule_source, RetryScheduleSource)
    assert any(isinstance(m, SentryMiddleware) for m in production.middlewares)


async def test_retry_schedule_source_shutdown_disconnects_its_pool() -> None:
    """taskiq_redis leaves the pool open, so every retry connection the worker or
    the scheduler opened would outlive the process's own shutdown."""
    source = create_retry_schedule_source()
    disconnect = AsyncMock()
    source._connection_pool.disconnect = disconnect  # type: ignore[method-assign]  # noqa: SLF001

    await source.shutdown()

    disconnect.assert_awaited_once()
    assert isinstance(source, ListRedisScheduleSource)
