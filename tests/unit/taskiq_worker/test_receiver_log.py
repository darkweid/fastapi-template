from collections.abc import Iterator
import logging

import pytest
from sqlalchemy.exc import DBAPIError
from taskiq import InMemoryBroker, TaskiqMessage, TaskiqResult
from taskiq.receiver import Receiver
from taskiq.receiver.params_parser import logger as params_logger
from taskiq.receiver.receiver import logger as receiver_logger

from src.core.errors.log_scrubbing import ScrubbedTracebacks
from taskiq_worker.app import TASKIQ_LOGGERS

REFUSED = DBAPIError("SELECT 1", None, ValueError("'secret@example.com'"))


async def refuse() -> None:
    raise REFUSED


async def break_down() -> None:
    raise RuntimeError("plain text of our own")


@pytest.fixture
def receiver_log(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    receiver_logger.addHandler(caplog.handler)
    yield
    receiver_logger.removeHandler(caplog.handler)


async def run(task: object, name: str) -> TaskiqResult:
    receiver = Receiver(broker=InMemoryBroker(), run_startup=False)
    message = TaskiqMessage(task_id="id", task_name=name, labels={}, args=[], kwargs={})
    return await receiver.run_task(task, message)  # type: ignore[arg-type]


def test_the_worker_filters_the_logger_taskiq_writes_to() -> None:
    assert {receiver_logger.name, params_logger.name} == set(TASKIQ_LOGGERS)
    for logger in (receiver_logger, params_logger):
        assert any(isinstance(item, ScrubbedTracebacks) for item in logger.filters)


@pytest.mark.parametrize("form", ["positional", "mapping"])
def test_an_error_passed_as_an_argument_is_scrubbed(form: str) -> None:
    args = (REFUSED, 1) if form == "positional" else ({"error": REFUSED, "n": 1},)
    message = "%s %d" if form == "positional" else "%(error)s %(n)d"
    record = logging.LogRecord("x", logging.ERROR, "x", 1, message, args, None)

    ScrubbedTracebacks().filter(record)

    assert record.getMessage() == "DBAPIError driver=ValueError 1"


async def test_the_receiver_logs_no_text_of_a_driver_error(
    receiver_log: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The receiver logs the exception of every failed task with its traceback,
    past whatever the task itself logged."""
    result = await run(refuse, "refuse")

    assert "Exception found while executing function" in caplog.text
    assert "DBAPIError driver=ValueError" in caplog.text
    assert "secret@example.com" not in caplog.text
    # A retry that names `types_of_exceptions` matches what the task raised.
    assert result.error is REFUSED


async def test_the_receiver_still_logs_an_ordinary_traceback(
    receiver_log: None, caplog: pytest.LogCaptureFixture
) -> None:
    await run(break_down, "break_down")

    assert "plain text of our own" in caplog.text
    assert "in break_down" in caplog.text
