import asyncio

from celery import Celery
from celery.signals import worker_init

from loggers import get_logger
from src.core.redis.cache.backend.redis_backend import RedisCacheBackend
from src.main.config import config
from src.main.sentry import init_sentry

logger = get_logger(__name__)

init_sentry()

DEFAULT_QUEUE = "default"
DEFAULT_WORKER_STATE_DB = "/data/celery-worker-state"

CELERY_INCLUDE_MODULES = (
    "src.user.tasks",
    "src.user.auth.tasks",
    "src.core.email_service.tasks",
)


def init_redis_cache(**kwargs: object) -> None:
    logger.info("Initializing RedisCacheBackend for Celery worker...")
    backend = RedisCacheBackend()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(backend.connect(config.redis.dsn))
    logger.info("RedisCacheBackend initialized.")


worker_init.connect(init_redis_cache)


def build_task_routes() -> dict[str, dict[str, str]]:
    return {}


def create_celery_app(module_name: str) -> Celery:
    celery_app = Celery(
        module_name,
        broker=config.rabbitmq.dsn,
        backend=config.redis.celery_dsn,
    )
    celery_app.conf.broker_connection_retry_on_startup = True
    celery_app.conf.update(
        include=list(CELERY_INCLUDE_MODULES),
        timezone="UTC",
        enable_utc=True,
        task_create_missing_queues=True,
        task_acks_late=True,
        task_send_sent_event=True,
        task_track_started=True,
        task_soft_time_limit=1500,
        task_time_limit=1800,
        task_always_eager=False,
        task_reject_on_worker_lost=True,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        task_default_queue=DEFAULT_QUEUE,
        task_routes=build_task_routes(),
    )
    return celery_app


def configure_worker_runtime(celery_app: Celery, *, worker_state_db: str) -> Celery:
    celery_app.conf.worker_state_db = worker_state_db
    return celery_app


celery_app = create_celery_app(__name__)
celery_app.set_default()
