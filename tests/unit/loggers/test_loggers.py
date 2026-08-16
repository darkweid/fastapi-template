import json
import logging
import sys

import pytest

import loggers
from src.core.request_context import request_id_var


@pytest.fixture(autouse=True)
def _reset_json_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # LOG_JSON is read once at import time into a module-level flag; tests
    # that flip it monkeypatch the flag directly rather than re-importing.
    monkeypatch.setattr(loggers, "LOG_JSON", False)


def _make_test_logger(name: str, *, plain_format: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(
        loggers._build_stream_handler(
            loggers._PLAIN_FORMAT if plain_format else loggers._LOG_FORMAT
        )
    )
    return logger


def _last_record_via_capture(logger: logging.Logger) -> logging.LogRecord:
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    try:
        logger.info("hello")
    finally:
        logger.removeHandler(handler)
    return captured[-1]


def test_request_id_filter_defaults_to_dash_outside_request_context() -> None:
    logger = _make_test_logger("test.logger.no_context")
    record = _last_record_via_capture(logger)
    assert record.request_id == "-"


def test_request_id_filter_reads_contextvar_when_set() -> None:
    logger = _make_test_logger("test.logger.with_context")
    token = request_id_var.set("abc-123")
    try:
        record = _last_record_via_capture(logger)
    finally:
        request_id_var.reset(token)
    assert record.request_id == "abc-123"


def test_get_logger_attaches_request_id_filter_to_its_handler() -> None:
    logger = loggers.get_logger("test.logger.get_logger_filter")
    handler = logger.handlers[0]
    assert any(isinstance(f, loggers.RequestIDFilter) for f in handler.filters)


def test_worker_style_logger_unaffected_by_missing_request_context() -> None:
    # taskiq_worker imports loggers too; records logged outside any request
    # (e.g. from a task) must not error and must fall back to "-".
    logger = _make_test_logger("test.logger.worker_style", plain_format=True)
    assert request_id_var.get() is None
    record = _last_record_via_capture(logger)
    assert record.request_id == "-"


def test_json_formatter_round_trips_exact_field_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loggers, "LOG_JSON", True)
    logger = _make_test_logger("test.logger.json")
    token = request_id_var.set("json-req-id")
    try:
        record = _last_record_via_capture(logger)
    finally:
        request_id_var.reset(token)

    formatter = loggers.JsonLogFormatter()
    formatted = formatter.format(record)
    payload = json.loads(formatted)

    assert set(payload) == {
        "timestamp",
        "level",
        "logger",
        "message",
        "request_id",
        "process",
    }
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger.json"
    assert payload["message"] == "hello"
    assert payload["request_id"] == "json-req-id"
    assert isinstance(payload["process"], int)


def test_json_formatter_includes_exc_info_when_present() -> None:
    formatter = loggers.JsonLogFormatter()
    logger = logging.getLogger("test.logger.json_exc")
    try:
        raise ValueError("boom")
    except ValueError:
        record = logger.makeRecord(
            logger.name,
            logging.ERROR,
            __file__,
            0,
            "failure",
            (),
            exc_info=sys.exc_info(),
        )
    record.request_id = "-"
    formatted = formatter.format(record)
    payload = json.loads(formatted)

    assert "exc_info" in payload
    assert "ValueError: boom" in payload["exc_info"]


def test_json_formatter_omits_exc_info_when_absent() -> None:
    formatter = loggers.JsonLogFormatter()
    logger = logging.getLogger("test.logger.json_no_exc")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, "fine", (), exc_info=None
    )
    record.request_id = "-"
    formatted = formatter.format(record)
    payload = json.loads(formatted)

    assert "exc_info" not in payload


def test_get_logger_uses_json_formatter_when_log_json_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loggers, "LOG_JSON", True)
    handler = loggers._build_stream_handler(loggers._LOG_FORMAT)
    assert isinstance(handler.formatter, loggers.JsonLogFormatter)


def test_get_logger_uses_text_formatter_when_log_json_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loggers, "LOG_JSON", False)
    handler = loggers._build_stream_handler(loggers._LOG_FORMAT)
    assert isinstance(handler.formatter, logging.Formatter)
    assert not isinstance(handler.formatter, loggers.JsonLogFormatter)
