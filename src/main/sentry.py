from collections.abc import Callable, Sequence
import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.types import Event, Hint

from loggers import get_logger
from src.core.errors.describe import reportable
from src.main.config import config

logger = get_logger(__name__)

_sentry_initialized = False


BeforeSend = Callable[[Event, Hint], Event | None]


def _scrub_event(event: Event, hint: Hint) -> None:
    """
    Take the text of an error that echoes input out of an event made from it.

    Sentry captures an exception on its own where no handler of ours runs, such
    as one raised after the response started. The exception is renamed in place
    and not rebuilt from the stand-in: by now the event is serialized and its
    frames are marked in_app, which the SDK would not do again.
    """
    exc_info = hint.get("exc_info")
    error = exc_info[1] if exc_info else None
    if error is None:
        return
    stand_in = reportable(error)
    if stand_in is error:
        return
    # The error itself comes last; the ones before it are its chain, and the
    # mechanism only says how they were linked.
    values = (event.get("exception") or {}).get("values") or [{}]
    exception: dict[str, Any] = {
        "type": type(stand_in).__name__,
        "module": type(stand_in).__module__,
        "value": str(stand_in),
    }
    if stacktrace := values[-1].get("stacktrace"):
        exception["stacktrace"] = stacktrace
    if mechanism := values[-1].get("mechanism"):
        exception["mechanism"] = {
            key: mechanism[key] for key in ("type", "handled") if key in mechanism
        }
    event["exception"] = {"values": [exception]}
    # A log call may have put the error into its message, arguments or `extra`.
    if "logentry" in event:
        event["logentry"] = {"message": str(stand_in)}
    if "log_record" in hint:
        event.pop("extra", None)


def _scrubbing(before_send: BeforeSend | None) -> BeforeSend:
    """`before_send` of the caller first, so what it returns is scrubbed too."""

    def scrub_and_send(event: Event, hint: Hint) -> Event | None:
        edited = before_send(event, hint) if before_send else event
        if edited is not None:
            _scrub_event(edited, hint)
        return edited

    return scrub_and_send


def init_sentry(
    *,
    disabled_integrations: Sequence[Integration] = (),
    before_send: BeforeSend | None = None,
    before_send_transaction: BeforeSend | None = None,
) -> None:
    """
    Initialize the Sentry client once using environment variables.

    Call it before the application is built: the Starlette and FastAPI
    integrations patch classes the middleware stack and the routes are made of.

    `disabled_integrations`, `before_send` and `before_send_transaction` are
    for a process whose requests carry what no event may hold: the first
    turns off more integrations Sentry would enable by itself, the other two edit
    every error and every transaction before it leaves. An error is scrubbed
    after `before_send`, with or without one.
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
            # No status code is a failure by itself: the exception handlers and
            # middlewares report what is one. The default would file an event
            # per 503 a probe answers and a second one per InfrastructureException.
            StarletteIntegration(failed_request_status_codes=set()),
            FastApiIntegration(failed_request_status_codes=set()),
        ],
        # aiohttp breadcrumbs and spans carry the full URL, and a third-party API
        # may take its token there; trace headers would also go out to it.
        disabled_integrations=[AioHttpIntegration(), *disabled_integrations],
        # The other HTTP integrations send trace headers to every host by default.
        trace_propagation_targets=[],
        before_send=_scrubbing(before_send),
        before_send_transaction=before_send_transaction,
    )
    _sentry_initialized = True
    logger.info("Sentry initialized.")
