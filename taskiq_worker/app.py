"""Worker entrypoint: `taskiq worker taskiq_worker.app:broker`.

Every task module must be imported here - a task module not imported here is
invisible to the worker.
"""

import src.core.email_service.tasks  # noqa: F401
from src.main.sentry import init_sentry
import src.user.auth.tasks  # noqa: F401
import src.user.tasks  # noqa: F401
from taskiq_worker.broker import broker

init_sentry()

__all__ = ["broker"]
