from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from src.main.config import config
from src.main.web import get_application

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture
def client() -> TestClient:
    return TestClient(get_application())


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_require_credentials(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="API docs"'


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_reject_wrong_credentials(client: TestClient, path: str) -> None:
    response = client.get(path, auth=("docs-user", "wrong-password"))

    assert response.status_code == 401


@pytest.mark.parametrize("path", DOCS_PATHS)
def test_docs_are_served_with_credentials(client: TestClient, path: str) -> None:
    response = client.get(
        path,
        auth=(config.app.DOCS_USERNAME, config.app.DOCS_PASSWORD),
    )

    assert response.status_code == 200


def test_openapi_schema_is_served_with_credentials(client: TestClient) -> None:
    response = client.get(
        "/openapi.json",
        auth=(config.app.DOCS_USERNAME, config.app.DOCS_PASSWORD),
    )

    assert "/v1/users/auth/login" in response.json()["paths"]


def test_docs_are_not_published_without_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.app, "DOCS_PASSWORD", "")
    client = TestClient(get_application())

    for path in DOCS_PATHS:
        assert client.get(path).status_code == 404


def test_docs_are_open_in_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.app, "DEBUG", True)
    client = TestClient(get_application())

    for path in DOCS_PATHS:
        assert client.get(path).status_code == 200
