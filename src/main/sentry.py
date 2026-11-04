from collections.abc import Callable, Sequence
import logging

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.types import Event, Hint

from loggers import get_logger
from src.main.config import config

logger = get_logger(__name__)

_sentry_initialized = False


def init_sentry(
    *,
    disabled_integrations: Sequence[Integration] = (),
    before_send: Callable[[Event, Hint], Event | None] | None = None,
    before_send_transaction: Callable[[Event, Hint], Event | None] | None = None,
) -> None:
    """
    Initialize the Sentry client once using environment variables.

    `disabled_integrations`, `before_send` and `before_send_transaction` are
    for a process whose requests carry what no event may hold: the first
    turns off integrations Sentry would enable by itself, the other two edit
    every error and every transaction before it leaves.
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
        disabled_integrations=list(disabled_integrations),
        before_send=before_send,
        before_send_transaction=before_send_transaction,
    )
    _sentry_initialized = True
    logger.info("Sentry initialized.")
