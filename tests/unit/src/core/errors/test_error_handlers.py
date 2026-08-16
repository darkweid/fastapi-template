import json
import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
import pytest
import sentry_sdk

from src.core.errors import exceptions as exc, handlers
from src.core.errors.codes import ErrorCode
from src.core.errors.handlers import (
    handle_core_exception,
    handle_request_validation_exception,
    handle_validation_error,
)
import src.user.auth.errors  # noqa: F401 - register domain subclasses for the walk; every new domain errors module must be imported here the same way


class SampleModel(BaseModel):
    field: int


def make_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
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


def make_request_validation_error() -> RequestValidationError:
    return RequestValidationError(
        [
            {
                "loc": ("body", "field"),
                "msg": "value is not a valid integer",
                "type": "type_error.integer",
            }
        ]
    )


def make_pydantic_error() -> ValidationError:
    try:
        SampleModel.model_validate({"field": "bad"})
    except ValidationError as error:
        return error
    raise AssertionError("expected ValidationError")


@pytest.fixture(autouse=True)
def _patch_response_logger(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    logger = logging.getLogger("response_logger_test")
    logger.handlers = []
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    monkeypatch.setattr(handlers, "response_logger", logger)
    return logger


def test_format_log_message_masks_sensitive_data() -> None:
    request = make_request(headers=[(b"x-request-id", b"req-123")])

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
    request = make_request()
    long_message = "a" * 600

    message = handlers.format_log_message(request, "error", long_message)

    assert message.endswith("...")
    assert message.count("a") == 497


async def test_generic_handler_serializes_declared_status_and_code() -> None:
    response = await handle_core_exception(
        make_request(), exc.InstanceNotFoundException("User not found.")
    )
    assert response.status_code == 404
    assert json.loads(response.body) == {
        "code": "not_found",
        "message": "User not found.",
    }


async def test_generic_handler_defaults_message() -> None:
    response = await handle_core_exception(make_request(), exc.CoreException())
    assert json.loads(response.body) == {
        "code": "processing_error",
        "message": "No additional details available",
    }


async def test_generic_handler_emits_declared_headers_and_extras() -> None:
    response = await handle_core_exception(
        make_request(),
        exc.TooManyRequestsException("Too many requests", retry_after=30),
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert json.loads(response.body) == {
        "code": "rate_limited",
        "message": "Too many requests",
        "retry_after": 30,
    }


async def test_generic_handler_captures_sentry_only_when_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", captured.append)

    await handle_core_exception(make_request(), exc.InfrastructureException("boom"))
    assert len(captured) == 1

    await handle_core_exception(make_request(), exc.ServiceUnavailableException("down"))
    assert len(captured) == 1


async def test_request_validation_handler_shapes_errors_list() -> None:
    validation_error = make_request_validation_error()
    response = await handle_request_validation_exception(
        make_request(), validation_error
    )
    body = json.loads(response.body)
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert body["message"] == "Request validation failed"
    assert all(set(item) == {"field", "message"} for item in body["errors"])


def test_format_validation_errors_keeps_non_leading_body_segment() -> None:
    # Only the leading "body" prefix FastAPI adds is dropped; a field that is
    # itself literally named "body" further down the path must survive.
    errors = [
        {
            "loc": ("body", "payload", "body"),
            "msg": "field required",
            "type": "missing",
        }
    ]

    formatted = handlers._format_validation_errors(errors)

    assert formatted == [{"field": "payload.body", "message": "field required"}]


async def test_backend_validation_handler_hides_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", captured.append)
    response = await handle_validation_error(make_request(), make_pydantic_error())
    assert response.status_code == 500
    assert json.loads(response.body) == {
        "code": "internal_error",
        "message": "Unexpected error",
    }
    assert len(captured) == 1


def test_format_error_response_defaults_message_when_blank() -> None:
    body = handlers.format_error_response(ErrorCode.NOT_FOUND, None)
    assert body == {
        "code": ErrorCode.NOT_FOUND,
        "message": "No additional details available",
    }


def _all_core_exception_classes() -> list[type[exc.CoreException]]:
    found: list[type[exc.CoreException]] = []
    stack: list[type[exc.CoreException]] = [exc.CoreException]
    while stack:
        current = stack.pop()
        found.append(current)
        stack.extend(current.__subclasses__())
    return found


ALLOWED_EXTRA_KEYS = {"retry_after", "errors"}


@pytest.mark.parametrize("exception_class", _all_core_exception_classes())
async def test_every_exception_answers_the_error_contract(
    exception_class: type[exc.CoreException],
) -> None:
    response = await handle_core_exception(make_request(), exception_class("boom"))
    body = json.loads(response.body)
    assert set(body) - ALLOWED_EXTRA_KEYS == {"code", "message"}
    assert body["code"] in set(ErrorCode)
    assert response.status_code == exception_class.status_code


async def test_http_exception_handler_translates_401_and_passes_headers() -> None:
    # FastAPI's Security utilities raise a bare HTTPException when credentials
    # are missing; this is the smoke-tested regression for GET /v1/users/me.
    unauthenticated = HTTPException(
        status_code=401,
        detail="Not authenticated",
        headers={"WWW-Authenticate": 'Basic realm="docs"'},
    )
    response = await handlers.handle_http_exception(make_request(), unauthenticated)
    assert response.status_code == 401
    assert json.loads(response.body) == {
        "code": "unauthorized",
        "message": "Not authenticated",
    }
    assert response.headers["WWW-Authenticate"] == 'Basic realm="docs"'


async def test_http_exception_handler_translates_404() -> None:
    # Starlette's router raises this for an unmatched path.
    not_found = HTTPException(status_code=404, detail="Not Found")
    response = await handlers.handle_http_exception(make_request(), not_found)
    assert response.status_code == 404
    assert json.loads(response.body) == {"code": "not_found", "message": "Not Found"}


async def test_http_exception_handler_translates_405() -> None:
    wrong_method = HTTPException(status_code=405, detail="Method Not Allowed")
    response = await handlers.handle_http_exception(make_request(), wrong_method)
    assert response.status_code == 405
    assert json.loads(response.body)["code"] == "method_not_allowed"


async def test_http_exception_handler_maps_unknown_status_to_processing_error() -> None:
    teapot = HTTPException(status_code=418, detail="I'm a teapot")
    response = await handlers.handle_http_exception(make_request(), teapot)
    assert json.loads(response.body)["code"] == "processing_error"


async def test_http_exception_handler_maps_5xx_to_internal_error() -> None:
    bad_gateway = HTTPException(status_code=502, detail="Bad Gateway")
    response = await handlers.handle_http_exception(make_request(), bad_gateway)
    assert json.loads(response.body)["code"] == "internal_error"


async def test_http_exception_handler_omits_body_for_bodyless_status() -> None:
    # 304 (and 204/1xx) must not carry a body; a JSON error payload there
    # would be a protocol violation.
    not_modified = HTTPException(status_code=304)
    response = await handlers.handle_http_exception(make_request(), not_modified)
    assert response.status_code == 304
    assert response.body == b""
