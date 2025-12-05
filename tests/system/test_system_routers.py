from collections.abc import AsyncGenerator
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.database.session import get_session
from src.system import routers
from src.system.dependencies import get_health_service
from src.system.schemas import HealthCheckResponse


class FakeHealthService:
    async def get_status(self, session) -> HealthCheckResponse:
        return HealthCheckResponse(status="ok")


async def get_session_override() -> AsyncGenerator[None]:
    yield None


def get_health_service_override() -> FakeHealthService:
    return FakeHealthService()


def _build_client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_check_health_endpoint() -> None:
    app = FastAPI()
    app.include_router(routers.router)
    app.dependency_overrides[get_health_service] = get_health_service_override
    app.dependency_overrides[get_session] = get_session_override
    client = _build_client(app)

    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    head_response = client.head("/health/")
    assert head_response.status_code == 200


def test_get_utc_time(monkeypatch) -> None:
    fixed_now = datetime(2024, 1, 1, 12, 30, 45, 123456, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(routers, "get_utc_now", lambda: fixed_now)

    app = FastAPI()
    app.include_router(routers.router)
    client = _build_client(app)

    response = client.get("/time/")

    assert response.status_code == 200
    assert response.json() == {"time": "2024-01-01T12:30:45+00:00"}
