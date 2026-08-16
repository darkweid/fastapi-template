from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from fastapi import FastAPI
import httpx2
import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.core.database.session import get_session
from src.core.redis.dependencies import get_redis_client
from src.system import routers
from src.system.dependencies import get_health_service
from src.system.schemas import HealthCheckResponse
from tests.fakes.db import FakeAsyncSession
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeHealthService:
    def __init__(self, response: HealthCheckResponse) -> None:
        self.response = response

    async def get_status(self) -> HealthCheckResponse:
        return self.response


class DeadRedis:
    async def ping(self) -> bool:
        raise RuntimeError("down")


@pytest.mark.asyncio
async def test_check_liveness_endpoint(app: FastAPI) -> None:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/live/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        head_response = await client.head("/live/")
        assert head_response.status_code == 200


@pytest.mark.asyncio
async def test_check_liveness_survives_a_dead_redis(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
) -> None:
    """The regression this split exists to prevent: a dependency outage must
    not make the container look dead."""
    dependency_overrides.set(get_redis_client, ProvideValue(DeadRedis()))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/live/")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_check_readiness_endpoint(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
) -> None:
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/ready/")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_check_readiness_returns_503_when_database_is_down(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
) -> None:
    fake_session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/ready/")

        assert response.status_code == 503
        assert response.json()["code"] == "service_unavailable"


@pytest.mark.asyncio
async def test_check_health_endpoint(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
) -> None:
    status = HealthCheckResponse(status="degraded", postgres=True, redis=False)
    dependency_overrides.set(
        get_health_service, ProvideValue(FakeHealthService(status))
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/health/")
        assert response.status_code == 200
        assert response.json() == {
            "status": "degraded",
            "postgres": True,
            "redis": False,
        }

        head_response = await client.head("/health/")
        assert head_response.status_code == 200


@pytest.mark.asyncio
async def test_check_health_keeps_answering_when_postgres_is_down(
    app: FastAPI,
    dependency_overrides: DependencyOverrides,
    fake_session: FakeAsyncSession,
    fake_redis,
) -> None:
    """The detailed report must survive the outage it is meant to describe."""
    fake_session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(get_redis_client, ProvideValue(fake_redis))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/health/")

        assert response.status_code == 200
        assert response.json() == {
            "status": "degraded",
            "postgres": False,
            "redis": True,
        }


@pytest.mark.asyncio
async def test_get_utc_time(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = datetime(2024, 1, 1, 12, 30, 45, 123456, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(routers, "get_utc_now", ProvideValue(fixed_now))

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/time/")

        assert response.status_code == 200
        assert response.json() == {"time": "2024-01-01T12:30:45+00:00"}
