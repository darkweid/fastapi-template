from taskiq.schedule_sources import LabelScheduleSource

from taskiq_worker.app import broker
from taskiq_worker.scheduler import scheduler

EXPECTED_TASKS = {
    "cleanup_unverified_users",
    "send_verification_email",
    "send_reset_password_email",
    "send_email",
    "send_email_with_file",
}


def test_app_registers_every_task() -> None:
    assert EXPECTED_TASKS <= set(broker.get_all_tasks())


def test_scheduler_reads_labels_from_broker() -> None:
    assert any(isinstance(source, LabelScheduleSource) for source in scheduler.sources)
