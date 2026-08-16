import json
import os
import subprocess
import sys

from taskiq.schedule_sources import LabelScheduleSource

from taskiq_worker.scheduler import scheduler

EXPECTED_TASKS = {
    "cleanup_unverified_users",
    "send_verification_email",
    "send_reset_password_email",
    "send_email",
    "send_email_with_file",
    "outbox_sweeper",
    "outbox_purge",
}

_LIST_REGISTERED_TASKS_SCRIPT = (
    "import json\n"
    "from taskiq_worker.app import broker\n"
    "print(json.dumps(list(broker.get_all_tasks())))\n"
)


def test_app_registers_every_task() -> None:
    """`taskiq_worker.app`'s own registration imports must register every task.

    This runs in a fresh subprocess that imports only `taskiq_worker.app`. In
    the full suite, other test modules import the task modules at collection
    time and populate the shared `broker` singleton before this test would
    run, so an in-process assertion here would pass even if `app.py` were
    missing a registration import - it would silently stop registering tasks
    for the real worker CLI target without any test catching it.
    """
    result = subprocess.run(
        [sys.executable, "-c", _LIST_REGISTERED_TASKS_SCRIPT],
        capture_output=True,
        text=True,
        env={**os.environ, "TESTING": "true"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    # loggers.get_logger writes to stdout by design (containers own log
    # shipping), so any import-time log line lands ahead of the JSON payload;
    # the payload is always the script's last printed line.
    json_line = result.stdout.strip().splitlines()[-1]
    registered_tasks = set(json.loads(json_line))
    assert EXPECTED_TASKS <= registered_tasks


def test_scheduler_reads_labels_from_broker() -> None:
    assert any(isinstance(source, LabelScheduleSource) for source in scheduler.sources)
