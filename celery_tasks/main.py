from celery import Celery

from src.main.config import config
from loggers import get_logger

logger = get_logger(__name__)


redis_url = config.redis.dsn
rabbitmq_url = config.rabbitmq.dsn

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
    ],
)
