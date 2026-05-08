from celery.schedules import crontab

from celery_tasks.workers.common import celery_app

celery_app.conf.beat_schedule = {
    "cleanup_unverified_users_every_10_hours": {
        "task": "cleanup_unverified_users",
        "schedule": crontab(minute=0, hour="*/10"),
    },
}
