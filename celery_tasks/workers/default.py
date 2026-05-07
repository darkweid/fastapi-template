from celery_tasks.workers.common import (
    DEFAULT_WORKER_STATE_DB,
    celery_app,
    configure_worker_runtime,
)

configure_worker_runtime(celery_app, worker_state_db=DEFAULT_WORKER_STATE_DB)
