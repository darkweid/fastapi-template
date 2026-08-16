from datetime import datetime, timezone
import json
import logging
import os
import sys
from typing import Any

from src.core.request_context import get_request_id

_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s]|[%(process)d]|[%(request_id)s]| %(name)s: %(message)s"
)
_PLAIN_FORMAT = "%(asctime)s [%(process)d]|[%(request_id)s]| %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

# Structured JSON logs for log aggregators that parse stdout (e.g. a
# container platform shipping to a log index); text formats stay the default
# for local/dev readability. Parsed with the same tolerant boolean spelling
# as LOG_LEVEL's siblings elsewhere in the app.
LOG_JSON = os.environ.get("LOG_JSON", "false").strip().lower() in {
    "1",
    "true",
    "t",
    "yes",
    "y",
    "on",
}


class RequestIDFilter(logging.Filter):
    """Stamps every record with the id of the request being handled.

    Outside a request (worker tasks, startup/shutdown code) there is no
    contextvar value, so records fall back to "-" rather than being missing
    the field entirely - formats reference %(request_id)s unconditionally.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


class JsonLogFormatter(logging.Formatter):
    """Renders one JSON object per line instead of the text formats."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "process": record.process,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_formatter(fmt: str) -> logging.Formatter:
    if LOG_JSON:
        return JsonLogFormatter()
    return logging.Formatter(fmt, _DATE_FORMAT)


def _build_stream_handler(fmt: str) -> logging.StreamHandler:  # type: ignore[type-arg]
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(_build_formatter(fmt))
    handler.addFilter(RequestIDFilter())
    return handler


def get_logger(name: str, *, plain_format: bool = False) -> logging.Logger:
    """Stdout-only logger. Containers own log shipping; files and rotation
    are deliberately not this process's job."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(log_level)
    logger.addHandler(
        _build_stream_handler(_PLAIN_FORMAT if plain_format else _LOG_FORMAT)
    )
    logger.propagate = False
    return logger
