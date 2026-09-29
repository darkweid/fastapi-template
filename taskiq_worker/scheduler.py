"""Scheduler entrypoint: `taskiq scheduler taskiq_worker.scheduler:scheduler`.

Run exactly one instance: taskiq has no duplicate-fire protection, N
schedulers fire every cron N times. Delayed retries also flow through this
process (SmartRetryMiddleware writes them to retry_schedule_source).
"""

from taskiq import TaskiqScheduler
from taskiq.abc.schedule_source import ScheduleSource
from taskiq.schedule_sources import LabelScheduleSource

from src.main.config import config
from taskiq_worker.app import broker
from taskiq_worker.broker import retry_schedule_source
from taskiq_worker.heartbeat import HeartbeatScheduleSource


def build_scheduler_sources() -> list[ScheduleSource]:
    sources: list[ScheduleSource] = [
        LabelScheduleSource(broker),
        HeartbeatScheduleSource(config.redis.tasks_dsn),
    ]
    if retry_schedule_source is not None:
        sources.append(retry_schedule_source)
    return sources


scheduler = TaskiqScheduler(broker=broker, sources=build_scheduler_sources())
