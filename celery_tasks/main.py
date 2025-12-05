from celery import Celery
from celery.schedules import crontab
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from loggers import get_logger
from src.main.config import config
from src.main.sentry import init_sentry

init_sentry()
logger = get_logger(__name__)


redis_url = config.redis.dsn
rabbitmq_url = config.rabbitmq.dsn

# Async DB engine and session
engine = create_async_engine(config.postgres.dsn_async)
local_async_session = async_sessionmaker(bind=engine, expire_on_commit=False)

celery_app = Celery(__name__, broker=rabbitmq_url, backend=redis_url)

celery_app.conf.broker_connection_retry_on_startup = True
celery_app.conf.update(
    task_create_missing_queues=True,
    task_acks_late=True,
    task_send_sent_event=True,
    task_track_started=True,
    task_time_limit=1800,
    task_always_eager=False,  # False for async task execution
)

celery_app.conf.update(
    include=[
        "src.user.tasks",
        "src.core.email_service.tasks",
    ],
    timezone="UTC",
    enable_utc=True,
)

celery_app.conf.beat_schedule = {
    "cleanup_unverified_users_every_10_hours": {
        "task": "cleanup_unverified_users",
        "schedule": crontab(minute=0, hour="*/10"),
    },
}
