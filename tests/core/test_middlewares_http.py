from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.core.middleware as middleware  # noqa: E402


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


def _make_app(exception_factory) -> FastAPI:
    app = FastAPI()
    middleware.register_middlewares(app)

    @app.get("/boom")
    async def boom() -> PlainTextResponse:  # type: ignore[return-type]
        raise exception_factory()

    @app.get("/ok")
    async def ok() -> PlainTextResponse:
        return PlainTextResponse("ok")

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
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == "frame-ancestors 'none'"


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
