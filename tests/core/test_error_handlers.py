import json
import logging
from unittest.mock import MagicMock

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
import pytest

from src.core.errors import handlers
from src.core.errors.exceptions import (
    AccessForbiddenException,
    CoreException,
    FilteringError,
    InfrastructureException,
    InstanceAlreadyExistsException,
    InstanceNotFoundException,
    InstanceProcessingException,
    NotAcceptableException,
    PayloadTooLargeException,
    PermissionDeniedException,
    TooManyRequestsException,
    UnauthorizedException,
)


class SampleModel(BaseModel):
    field: int


class LogMessageWithPath:
    def __init__(self, original):
        self._original = original

    def __call__(
        self,
        request: Request,
        error_type: str,
        message: str | None,
        additional_info: dict[str, object] | None = None,
        include_request_path: bool = False,
    ) -> str:
        return self._original(
            request,
            error_type,
            message,
            additional_info,
            include_request_path=True,
        )


def _build_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "http_version": "1.1",
        "scheme": "http",
        "path": "/v1/resource",
        "root_path": "",
        "raw_path": b"/v1/resource",
        "query_string": b"",
        "asgi": {"version": "3.0"},
        "headers": headers or [],
        "client": ("127.0.0.1", 8000),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _patch_response_logger(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    logger = logging.getLogger("response_logger_test")
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    monkeypatch.setattr(handlers, "response_logger", logger)
    return logger


def test_format_log_message_masks_sensitive_data() -> None:
    request = _build_request(headers=[(b"x-request-id", b"req-123")])

    message = handlers.format_log_message(
        request,
        "unauthorized",
        "token leaked",
        {"token": "secret", "note": "safe"},
        include_request_path=True,
    )

    assert "[req-123] [Unauthorized] GET /v1/resource | token leaked" in message
    assert "token=***" in message
    assert "note='safe'" in message


def test_format_log_message_truncates_long_text() -> None:
    request = _build_request()
    long_message = "a" * 600

    message = handlers.format_log_message(request, "error", long_message)

    assert message.endswith("...")
    assert message.count("a") == 497


@pytest.mark.asyncio
async def test_core_exception_handler(caplog: pytest.LogCaptureFixture) -> None:
    handler = handlers.CoreExceptionHandler()
    request = _build_request()
    caplog.set_level(logging.INFO, logger="response_logger_test")

    response = await handler(request, CoreException("failed to process"))

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "Bad request",
        "message": "failed to process",
    }
    assert any("Bad request" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_filtering_error_handler_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler = handlers.FilteringErrorHandler()
    request = _build_request()
    caplog.set_level(logging.WARNING, logger="response_logger_test")

    response = await handler(request, FilteringError("invalid filter"))

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": "Filtering error",
        "message": "invalid filter",
    }
    assert any(
        record.levelno == logging.WARNING and "Filtering error" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_cls,exc_cls,status,error_type,log_level,include_path",
    [
        (
            handlers.InstanceNotFoundExceptionHandler,
            InstanceNotFoundException,
            404,
            "Instance not found",
            logging.INFO,
            False,
        ),
        (
            handlers.InstanceAlreadyExistsExceptionHandler,
            InstanceAlreadyExistsException,
            409,
            "Instance already exists",
            logging.INFO,
            False,
        ),
        (
            handlers.InstanceProcessingExceptionHandler,
            InstanceProcessingException,
            400,
            "Instance processing error",
            logging.INFO,
            False,
        ),
        (
            handlers.UnauthorizedExceptionHandler,
            UnauthorizedException,
            401,
            "Unauthorized",
            logging.WARNING,
            True,
        ),
        (
            handlers.AccessForbiddenExceptionHandler,
            AccessForbiddenException,
            403,
            "Forbidden",
            logging.WARNING,
            True,
        ),
        (
            handlers.NotAcceptableExceptionHandler,
            NotAcceptableException,
            406,
            "Not Acceptable",
            logging.INFO,
            False,
        ),
        (
            handlers.PermissionDeniedExceptionHandler,
            PermissionDeniedException,
            403,
            "Permission Denied",
            logging.WARNING,
            True,
        ),
    ],
)
async def test_other_handlers(
    handler_cls: type[handlers.HandlerCallable],
    exc_cls: type[CoreException],
    status: int,
    error_type: str,
    log_level: int,
    include_path: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_instance = handler_cls()
    request = _build_request()
    caplog.set_level(log_level, logger="response_logger_test")

    if include_path:
        monkeypatch.setattr(
            handlers,
            "format_log_message",
            LogMessageWithPath(handlers.format_log_message),
        )

    response = await handler_instance(request, exc_cls("failure"))

    assert response.status_code == status
    assert json.loads(response.body) == {"error": error_type, "message": "failure"}
    assert any(
        record.levelno == log_level and error_type in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_infrastructure_exception_handler_captures_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = handlers.InfrastructureExceptionHandler()
    request = _build_request()
    capture = MagicMock()
    monkeypatch.setattr(handlers.sentry_sdk, "capture_exception", capture)

    response = await handler(request, InfrastructureException("infra fail"))

    assert response.status_code == 500
    assert json.loads(response.body) == {
        "error": "Infrastructure error",
        "message": "infra fail",
    }
    capture.assert_called_once()


@pytest.mark.asyncio
async def test_request_validation_exception_handler_returns_422() -> None:
    handler = handlers.RequestValidationExceptionHandler()
    request = _build_request()
    exc = RequestValidationError(
        [
            {
                "loc": ("body", "field"),
                "msg": "value is not a valid integer",
                "type": "type_error.integer",
            }
        ]
    )

    response = await handler(request, exc)

    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["detail"][0]["loc"] == ["body", "field"]
    assert payload["detail"][0]["msg"] == "value is not a valid integer"


@pytest.mark.asyncio
async def test_validation_error_exception_handler_returns_500_and_captures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = handlers.ValidationErrorExceptionHandler()
    request = _build_request()
    capture = MagicMock()
    monkeypatch.setattr(handlers.sentry_sdk, "capture_exception", capture)

    with pytest.raises(ValidationError) as exc_info:
        SampleModel.model_validate({"field": "bad"})

    response = await handler(request, exc_info.value)

    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": "Unexpected error"}
    capture.assert_called_once()


@pytest.mark.asyncio
async def test_payload_too_large_exception_handler_returns_413() -> None:
    handler = handlers.PayloadTooLargeExceptionHandler()
    request = _build_request()

    response = await handler(request, PayloadTooLargeException("too large"))

    assert response.status_code == 413
    assert json.loads(response.body) == {
        "error": "Payload too large",
        "message": "too large",
    }


@pytest.mark.asyncio
async def test_too_many_requests_exception_handler_sets_retry_after() -> None:
    handler = handlers.TooManyRequestsExceptionHandler()
    request = _build_request()

    response = await handler(
        request, TooManyRequestsException("slow down", retry_after=5)
    )

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "5"
    assert json.loads(response.body) == {
        "error": "Too Many Requests",
        "message": "slow down",
    }
