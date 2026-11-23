import logging

from src.core.errors.describe import reportable

# taskiq logs every exception a task raises, traceback and all, under this name.
TASKIQ_RECEIVER_LOGGER = "taskiq.receiver.receiver"


class ScrubbedTracebacks(logging.Filter):
    """
    Keep user data out of the tracebacks taskiq's receiver logs.

    Done in the log record and not on the exception: retry and Sentry
    middlewares still see the exception the task raised.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            error = reportable(record.exc_info[1])
            record.exc_info = (type(error), error, error.__traceback__)
        return True


def install_log_scrubbing() -> None:
    logging.getLogger(TASKIQ_RECEIVER_LOGGER).addFilter(ScrubbedTracebacks())
