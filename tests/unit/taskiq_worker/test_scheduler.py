from unittest.mock import MagicMock, patch

from taskiq.schedule_sources import LabelScheduleSource

from taskiq_worker import scheduler as scheduler_module
from taskiq_worker.heartbeat import HeartbeatScheduleSource
from taskiq_worker.scheduler import build_scheduler_sources, scheduler


def test_scheduler_uses_label_source() -> None:
    assert any(isinstance(s, LabelScheduleSource) for s in scheduler.sources)


def test_retry_source_included_when_present() -> None:
    retry_source = MagicMock()
    with patch.object(scheduler_module, "retry_schedule_source", retry_source):
        sources = build_scheduler_sources()
    assert retry_source in sources


def test_retry_source_absent_under_testing() -> None:
    # TESTING=true build: module-level retry_schedule_source is None.
    assert not [
        source
        for source in scheduler.sources
        if not isinstance(source, LabelScheduleSource | HeartbeatScheduleSource)
    ]


def test_scheduler_writes_its_heartbeat() -> None:
    """The scheduler healthcheck reads this heartbeat; without the source nothing
    writes it and every scheduler container turns unhealthy."""
    assert any(isinstance(s, HeartbeatScheduleSource) for s in scheduler.sources)
