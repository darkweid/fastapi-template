import logging
import os
from logging import Logger, FileHandler, StreamHandler
from typing import Any

from app.core.settings import settings

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE = os.path.join(LOG_DIR, "debug.log")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging_format = "%(asctime)s [%(levelname)s]|[%(process)d]| %(name)s: %(message)s"
time_logging_format = "%Y-%m-%d %H:%M:%S"

log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
file_log_level = getattr(logging, settings.log_level_file.upper(), logging.WARNING)


def get_file_handler() -> FileHandler:
    file_handler = logging.FileHandler(LOG_FILE, "a", "utf-8")
    file_handler.setLevel(file_log_level)
    file_handler.setFormatter(logging.Formatter(logging_format, time_logging_format))
    return file_handler


def get_stream_handler() -> StreamHandler:  # type: ignore
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(logging.Formatter(logging_format, time_logging_format))
    return stream_handler


def get_logger(name: Any) -> Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(log_level)
        logger.addHandler(get_file_handler())
        logger.addHandler(get_stream_handler())
        logger.propagate = False
    return logger
