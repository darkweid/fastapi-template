import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from loggers import get_logger
from src.main.config import config

logger = get_logger(__name__)

_sentry_initialized = False


def init_sentry() -> None:
    """
    Initialize the Sentry client once using environment variables.
    """
    global _sentry_initialized

    if _sentry_initialized:
        return

    if config.app.DEBUG or getattr(config.app, "TESTING", False):
        logger.info("DEBUG/TESTING enabled. Skipping Sentry initialization.")
        return

    if not config.sentry.SENTRY_ENABLED or not config.sentry.SENTRY_DSN:
        logger.info("Sentry disabled or DSN empty. Skipping Sentry initialization.")
        return

    sentry_sdk.init(
        dsn=config.sentry.SENTRY_DSN,
        environment=config.sentry.SENTRY_ENV,
        release=config.app.VERSION,
        # Frame locals and request bodies carry phone numbers, OTP codes and
        # passwords, and the default scrubber only matches exact key names at
        # the top level. send_default_pii does not cover either of them.
        include_local_variables=False,
        max_request_body_size="never",
        integrations=[
            LoggingIntegration(
                level=logging.INFO,  # breadcrumbs from INFO and up
                event_level=logging.CRITICAL,  # only CRITICAL+ logs become Sentry events; lower levels require explicit capture
            ),
        ],
    )
    _sentry_initialized = True
    logger.info("Sentry initialized.")
