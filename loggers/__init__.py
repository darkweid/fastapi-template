import logging
import os

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "log_files")
LOG_FILE = os.path.join(LOG_DIR, "debug.log")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

logging_format = "%(asctime)s [%(levelname)s]|[PID:%(process)d]| %(name)s: %(message)s"
time_logging_format = "%Y-%m-%d %H:%M:%S"


def get_file_handler():
    file_handler = logging.FileHandler(LOG_FILE, "a", "utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter(logging_format, time_logging_format))
    return file_handler


def get_stream_handler():
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(logging.Formatter(logging_format, time_logging_format))
    return stream_handler


def get_logger(name):
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(get_file_handler())
        logger.addHandler(get_stream_handler())
        logger.propagate = False
    return logger
