from taskiq import InMemoryBroker
from taskiq.abc.broker import AsyncBroker
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from src.main.config import config
from taskiq_worker.middlewares import SentryMiddleware

RESULT_TTL_SECONDS = 3600
# XACK does not remove entries from a Redis stream, so without maxlen the queue
# stream grows forever. Approximate trimming on XADD keeps it bounded.
STREAM_MAXLEN = 10_000


def create_production_broker() -> AsyncBroker:
    """Assemble the Redis Streams broker used outside tests."""
    stream_broker = RedisStreamBroker(
        url=config.redis.tasks_dsn,
        maxlen=STREAM_MAXLEN,
        approximate=True,
    ).with_result_backend(
        RedisAsyncResultBackend(
            redis_url=config.redis.tasks_dsn,
            result_ex_time=RESULT_TTL_SECONDS,
        )
    )
    stream_broker.add_middlewares(
        SmartRetryMiddleware(
            default_retry_count=3,
            default_delay=60,
            use_jitter=True,
        ),
        SentryMiddleware(),
    )
    return stream_broker


def create_test_broker() -> AsyncBroker:
    """In-memory broker: .kiq() awaits the task inline, no Redis involved."""
    return InMemoryBroker(await_inplace=True)


broker: AsyncBroker = (
    create_test_broker() if config.app.TESTING else create_production_broker()
)
