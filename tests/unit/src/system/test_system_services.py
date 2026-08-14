from __future__ import annotations

import socket
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.core.errors.exceptions import ServiceUnavailableException
from src.system.repositories import SystemRepository
from src.system.services import HealthService, ensure_postgres_ready
from tests.fakes.db import FakeAsyncSession


class RedisOk:
    async def ping(self) -> bool:
        return True


class RedisFail:
    async def ping(self) -> bool:
        raise RuntimeError("down")


@pytest.mark.asyncio
async def test_health_service_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    service = HealthService(redis_client=RedisOk(), repository=SystemRepository())

    result = await service.get_status(session=session)

    assert result.status == "ok"
    assert result.postgres is True
    assert result.redis is True
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_health_service_redis_failure_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    service = HealthService(redis_client=RedisFail(), repository=SystemRepository())

    result = await service.get_status(session=session)

    assert result.status == "degraded"
    assert result.postgres is True
    assert result.redis is False
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_health_service_postgres_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
    service = HealthService(redis_client=RedisOk(), repository=SystemRepository())

    with pytest.raises(ServiceUnavailableException):
        await service.get_status(session=session)

    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_postgres_ready_passes_on_live_database() -> None:
    session = FakeAsyncSession()

    await ensure_postgres_ready(session, SystemRepository())


@pytest.mark.asyncio
async def test_ensure_postgres_ready_raises_on_dead_database() -> None:
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))

    with pytest.raises(ServiceUnavailableException):
        await ensure_postgres_ready(session, SystemRepository())


@pytest.mark.asyncio
async def test_ensure_postgres_ready_raises_on_unresolvable_host() -> None:
    """A host that does not resolve reaches the probe as a bare socket error."""
    session = FakeAsyncSession()
    session.execute = AsyncMock(
        side_effect=socket.gaierror("Name or service not known")
    )

    with pytest.raises(ServiceUnavailableException):
        await ensure_postgres_ready(session, SystemRepository())
