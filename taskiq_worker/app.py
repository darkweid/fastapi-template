"""Worker entrypoint: `taskiq worker taskiq_worker.app:broker`.

Every task module must be imported here - this is the registration point,
the successor of CELERY_INCLUDE_MODULES.
"""

import src.core.email_service.tasks  # noqa: F401
from src.main.sentry import init_sentry
import src.user.auth.tasks  # noqa: F401
import src.user.tasks  # noqa: F401
from taskiq_worker.broker import broker

init_sentry()

__all__ = ["broker"]
