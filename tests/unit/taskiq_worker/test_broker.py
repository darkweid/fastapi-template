from taskiq import InMemoryBroker
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import ListRedisScheduleSource, RedisStreamBroker

from taskiq_worker.broker import broker, create_production_broker
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
    assert isinstance(retry_middleware.schedule_source, ListRedisScheduleSource)
    assert any(isinstance(m, SentryMiddleware) for m in production.middlewares)
