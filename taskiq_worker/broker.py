from taskiq import InMemoryBroker
from taskiq.abc.broker import AsyncBroker
from taskiq.middlewares import SmartRetryMiddleware
from taskiq_redis import RedisScheduleSource, RedisStreamBroker

from src.main.config import config
from taskiq_worker.middlewares import SentryMiddleware

# taskiq_redis never removes acked entries (XACK only clears the consumer
# group's pending-entries list), so an uncapped stream grows forever. XADD
# MAXLEN is the only bound, but it trims by ID order alone and does not check
# ack state, so it can discard unacknowledged work once the backlog reaches
# this size. Task payloads here are small, so this is set high enough that no
# realistic worker outage or traffic burst should approach it - it guards
# against runaway growth, not against a live backlog.
STREAM_MAXLEN = 100_000

RETRY_DELAY_SECONDS = 60


def create_retry_schedule_source() -> RedisScheduleSource:
    return RedisScheduleSource(config.redis.tasks_dsn)


# Shared by SmartRetryMiddleware (writes one-shot retry schedules) and the
# scheduler (fires them when due). None under TESTING: the in-memory broker
# retries nothing and the scheduler never runs in tests.
retry_schedule_source: RedisScheduleSource | None = (
    None if config.app.TESTING else create_retry_schedule_source()
)


def create_production_broker() -> AsyncBroker:
    """Assemble the Redis Streams broker used outside tests.

    No result backend on purpose: nothing reads task results, so the default
    dummy backend (stores nothing) avoids keeping pickled payloads in Redis.
    Attach a RedisAsyncResultBackend here if a project ever needs results.
    """
    stream_broker = RedisStreamBroker(
        url=config.redis.tasks_dsn,
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
    # Retries are scheduled onto retry_schedule_source at now + delay; without
    # a schedule source RedisStreamBroker ignores the `delay` label and every
    # retry fires back-to-back. Delayed retries therefore require a running
    # scheduler process.
    stream_broker.add_middlewares(
        SmartRetryMiddleware(
            default_retry_count=3,
            default_delay=RETRY_DELAY_SECONDS,
            use_jitter=True,
            schedule_source=retry_schedule_source or create_retry_schedule_source(),
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
