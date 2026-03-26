from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.core.errors.exceptions import InfrastructureException
from src.system.services import HealthService
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
    monkeypatch.setattr("src.system.services.sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    service = HealthService(redis_client=RedisOk())

    result = await service.get_status(session=session)

    assert result.status == "ok"
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_health_service_redis_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("src.system.services.sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    service = HealthService(redis_client=RedisFail())

    with pytest.raises(InfrastructureException):
        await service.get_status(session=session)

    sentry_mock.assert_called_once()


@pytest.mark.asyncio
async def test_health_service_postgres_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("src.system.services.sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))
    service = HealthService(redis_client=RedisOk())

    with pytest.raises(InfrastructureException):
        await service.get_status(session=session)

    sentry_mock.assert_called_once()
