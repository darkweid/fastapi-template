from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import (
    IntegrityError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)

import src.core.middleware as middleware


class DummyUnique:
    sqlstate = "23505"
    detail = "Key (email)=(test@example.com) already exists."

    def __str__(self) -> str:
        return self.detail


class DummyNotNull:
    sqlstate = "23502"
    detail = 'null value in column "username" of relation "users" violates not-null constraint'
    column_name = "username"

    def __str__(self) -> str:
        return self.detail


class DummyForeignKey:
    sqlstate = "23503"
    detail = 'insert or update on table "foo" violates foreign key constraint'

    def __str__(self) -> str:
        return self.detail


class DummyCheck:
    sqlstate = "23514"
    detail = "check constraint violated"

    def __str__(self) -> str:
        return self.detail


class DummyUnknown:
    sqlstate = "99999"
    detail = "some integrity issue"

    def __str__(self) -> str:
        return self.detail


class DummyOperational:
    orig = "connection refused"

    def __str__(self) -> str:
        return str(self.orig)


class DummyProgramming:
    orig = "syntax error"

    def __str__(self) -> str:
        return str(self.orig)


class DummyTransientDatabaseError(RuntimeError):
    pass


def make_cached_statement_error() -> NotSupportedError:
    return NotSupportedError(
        "SELECT 1",
        {},
        DummyTransientDatabaseError(
            "cached statement plan is invalid due to a database schema or "
            "configuration change"
        ),
    )


def make_invalidated_connection_error() -> OperationalError:
    return OperationalError(
        "SELECT 1",
        {},
        DummyTransientDatabaseError("server closed the connection unexpectedly"),
        connection_invalidated=True,
    )


def _make_app(exception_factory) -> FastAPI:
    app = FastAPI()
    middleware.register_middlewares(app)

    @app.get("/boom")
    async def boom() -> PlainTextResponse:  # type: ignore[return-type]
        raise exception_factory()

    @app.get("/ok")
    async def ok() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/docs")
    async def docs() -> PlainTextResponse:
        return PlainTextResponse("docs")

    return app


def _make_database_error_app() -> FastAPI:
    app = FastAPI()
    app.state.cached_statement_attempts = 0
    app.state.invalidated_connection_attempts = 0
    app.state.integrity_attempts = 0
    middleware.register_middlewares(app)

    @app.get("/cached-statement-plan")
    async def cached_statement_plan() -> dict[str, int]:
        app.state.cached_statement_attempts += 1
        raise make_cached_statement_error()

    @app.get("/invalidated-connection")
    async def invalidated_connection() -> dict[str, int]:
        app.state.invalidated_connection_attempts += 1
        raise make_invalidated_connection_error()

    @app.get("/integrity-error")
    async def integrity_error() -> dict[str, int]:
        app.state.integrity_attempts += 1
        raise IntegrityError(
            "INSERT INTO test",
            {},
            DummyTransientDatabaseError("integrity failed"),
        )

    return app


@pytest.fixture(autouse=True)
def _mute_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        middleware.sentry_sdk, "capture_exception", lambda *_, **__: None
    )


def test_security_headers_added() -> None:
    app = _make_app(lambda: None)
    client = TestClient(app)

    resp = client.get("/ok")

    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert (
        resp.headers["Strict-Transport-Security"]
        == "max-age=31536000; includeSubDomains; preload"
    )
    assert (
        resp.headers["Content-Security-Policy"]
        == middleware.STRICT_CONTENT_SECURITY_POLICY
    )
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert (
        resp.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    )


def test_docs_route_uses_docs_content_security_policy() -> None:
    app = _make_app(lambda: None)
    client = TestClient(app)

    resp = client.get("/docs")

    assert resp.status_code == 200
    assert (
        resp.headers["Content-Security-Policy"]
        == middleware.DOCS_CONTENT_SECURITY_POLICY
    )


def test_integrity_unique_violation() -> None:
    app = _make_app(lambda: IntegrityError("msg", None, DummyUnique()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 409
    assert resp.json() == {"detail": "email"}


def test_integrity_not_null_violation() -> None:
    app = _make_app(lambda: IntegrityError("msg", None, DummyNotNull()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}


def test_integrity_foreign_key_violation() -> None:
    app = _make_app(lambda: IntegrityError("msg", None, DummyForeignKey()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 400
    assert resp.json() == {"detail": DummyForeignKey.detail}


def test_integrity_check_violation() -> None:
    app = _make_app(lambda: IntegrityError("msg", None, DummyCheck()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}


def test_integrity_unknown_violation_defaults_to_500() -> None:
    app = _make_app(lambda: IntegrityError("msg", None, DummyUnknown()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}


def test_operational_error() -> None:
    app = _make_app(lambda: OperationalError("msg", None, DummyOperational()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {
        "detail": "Database connection error. Please try again later."
    }


def test_programming_error() -> None:
    app = _make_app(lambda: ProgrammingError("msg", None, DummyProgramming()))  # type: ignore[arg-type]
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Database query error."}


def test_unexpected_error_middleware() -> None:
    class Unexpected(Exception):
        pass

    def factory() -> Exception:
        return Unexpected("boom")

    app = _make_app(factory)
    client = TestClient(app)

    resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}


def test_cached_statement_plan_error_is_not_retried() -> None:
    app = _make_database_error_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/cached-statement-plan")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}
    assert app.state.cached_statement_attempts == 1


def test_invalidated_connection_error_is_not_retried() -> None:
    app = _make_database_error_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/invalidated-connection")

    assert resp.status_code == 500
    assert resp.json() == {
        "detail": "Database connection error. Please try again later."
    }
    assert app.state.invalidated_connection_attempts == 1


def test_database_error_middleware_does_not_retry_integrity_error() -> None:
    app = _make_database_error_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/integrity-error")

    assert resp.status_code == 500
    assert resp.json() == {"detail": middleware.UNEXPECTED_ERROR_DETAIL}
    assert app.state.integrity_attempts == 1
