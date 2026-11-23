import logging

from src.core.errors.describe import reportable


class ScrubbedTracebacks(logging.Filter):
    """
    Keep user data out of the tracebacks a library logs itself.

    Done in the log record and not on the exception: whatever handles it after
    the record still sees the exception that was raised.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            error = reportable(record.exc_info[1])
            record.exc_info = (type(error), error, error.__traceback__)
        return True


def install_log_scrubbing(logger_name: str) -> None:
    """The exact logger the records are created on: a logger's filters do not
    see the records of its children."""
    logging.getLogger(logger_name).addFilter(ScrubbedTracebacks())
