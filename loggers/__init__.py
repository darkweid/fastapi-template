import logging
import os
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)s]|[%(process)d]| %(name)s: %(message)s"
_PLAIN_FORMAT = "%(asctime)s [%(process)d]| %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)


def _build_stream_handler(fmt: str) -> logging.StreamHandler:  # type: ignore[type-arg]
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(logging.Formatter(fmt, _DATE_FORMAT))
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
