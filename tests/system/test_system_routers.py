from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import httpx
import pytest

from src.core.database.session import get_session
from src.system import routers
from src.system.dependencies import get_health_service
from src.system.schemas import HealthCheckResponse
from tests.fakes.db import FakeAsyncSession
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeHealthService:
    async def get_status(self, session) -> HealthCheckResponse:
        return HealthCheckResponse(status="ok")


@pytest.mark.asyncio
async def test_check_health_endpoint(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
) -> None:
    dependency_overrides.set(get_health_service, ProvideValue(FakeHealthService()))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/health/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        head_response = await client.head("/health/")
        assert head_response.status_code == 200


@pytest.mark.asyncio
async def test_get_utc_time(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = datetime(2024, 1, 1, 12, 30, 45, 123456, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(routers, "get_utc_now", ProvideValue(fixed_now))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/time/")

        assert response.status_code == 200
        assert response.json() == {"time": "2024-01-01T12:30:45+00:00"}
