from taskiq import InMemoryBroker
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from taskiq_worker.broker import RESULT_TTL_SECONDS, broker, create_production_broker
from taskiq_worker.middlewares import SentryMiddleware


def test_testing_env_selects_inmemory_broker() -> None:
    # The suite runs with TESTING=true, so the module-level broker must be the
    # inline in-memory one.
    assert isinstance(broker, InMemoryBroker)


def test_production_broker_assembly() -> None:
    production = create_production_broker()

    assert isinstance(production, RedisStreamBroker)
    assert isinstance(production.result_backend, RedisAsyncResultBackend)
    middleware_types = {type(m) for m in production.middlewares}
    assert SmartRetryMiddleware in middleware_types
    assert SentryMiddleware in middleware_types


def test_result_ttl_is_one_hour() -> None:
    assert RESULT_TTL_SECONDS == 3600
