from unittest.mock import MagicMock, patch

from taskiq.schedule_sources import LabelScheduleSource

from taskiq_worker import scheduler as scheduler_module
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
    assert len(scheduler.sources) == 1
