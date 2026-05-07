import importlib


def test_common_worker_configures_shared_celery_app() -> None:
    common = importlib.import_module("celery_tasks.workers.common")

    assert common.DEFAULT_QUEUE == "default"
    assert common.DEFAULT_WORKER_STATE_DB == "/data/celery-worker-state"
    assert common.celery_app.conf.broker_connection_retry_on_startup is True
    assert common.celery_app.conf.task_default_queue == "default"
    assert common.celery_app.conf.task_create_missing_queues is True
    assert common.celery_app.conf.include == [
        "src.user.tasks",
        "src.user.auth.tasks",
        "src.core.email_service.tasks",
    ]


def test_default_worker_sets_state_db() -> None:
    default = importlib.import_module("celery_tasks.workers.default")

    assert default.celery_app.conf.worker_state_db == default.DEFAULT_WORKER_STATE_DB


def test_beat_worker_registers_cleanup_schedule() -> None:
    beat = importlib.import_module("celery_tasks.workers.beat")

    schedule = beat.celery_app.conf.beat_schedule
    assert schedule["cleanup_unverified_users_every_10_hours"]["task"] == (
        "cleanup_unverified_users"
    )
