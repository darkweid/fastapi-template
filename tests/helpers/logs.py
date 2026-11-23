from collections.abc import Iterator
from contextlib import contextmanager
import logging

import pytest


@contextmanager
def capture_logger(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> Iterator[None]:
    """`caplog` for a project logger, which does not propagate to the root one.

    `caplog.text` then holds whatever reached it, tracebacks included.
    """
    caplog.set_level(logging.DEBUG)
    level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(level)
