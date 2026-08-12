"""Scheduler entrypoint: `taskiq scheduler taskiq_worker.scheduler:scheduler`.

Run exactly one instance: taskiq has no duplicate-fire protection, N
schedulers fire every cron N times.
"""

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from taskiq_worker.app import broker

scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
