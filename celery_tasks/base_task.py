from typing import Any

from celery import Task

from loggers import get_logger

logger = get_logger(__name__)


class BaseTaskWithRetry(Task):  # type: ignore[misc]
    """Reusable base class with retry logic."""

    max_retries = 3
    default_retry_delay = 60

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:  # noqa: D401
        logger.error(
            "Task %s failed: %s. Task ID: %s args=%s kwargs=%s",
            self.name,
            exc,
            task_id,
            args,
            kwargs,
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)
