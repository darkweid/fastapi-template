from collections.abc import Iterator
import logging

from src.core.errors.describe import reportable


def _arguments(record: logging.LogRecord) -> Iterator[object]:
    if isinstance(record.args, dict):
        yield from record.args.values()
    elif record.args:
        yield from record.args


def logged_stand_in(record: logging.LogRecord) -> BaseException | None:
    """
    The stand-in of the first error that echoes input the record was logged
    with, as the traceback, an argument or an `extra`; None when there is none.

    Text a library put into the message itself, without passing the error, is
    not recognized.
    """
    raised = record.exc_info[1] if record.exc_info else None
    for item in (raised, *_arguments(record), *vars(record).values()):
        if isinstance(item, BaseException):
            stand_in = reportable(item)
            if stand_in is not item:
                return stand_in
    return None


def _scrubbed_argument(argument: object) -> object:
    return reportable(argument) if isinstance(argument, BaseException) else argument


class ScrubbedTracebacks(logging.Filter):
    """
    Keep user data out of the errors a library logs itself, as a traceback or
    as an argument of the message.

    Done in the log record and not on the exception: whatever handles it after
    the record still sees the exception that was raised.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            error = reportable(record.exc_info[1])
            record.exc_info = (type(error), error, error.__traceback__)
        if isinstance(record.args, dict):
            record.args = {
                key: _scrubbed_argument(value) for key, value in record.args.items()
            }
        elif record.args:
            record.args = tuple(_scrubbed_argument(item) for item in record.args)
        return True


def install_log_scrubbing(logger_name: str) -> None:
    """The exact logger the records are created on: a logger's filters do not
    see the records of its children."""
    logging.getLogger(logger_name).addFilter(ScrubbedTracebacks())
