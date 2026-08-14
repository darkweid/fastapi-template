from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.core.errors.exceptions import ServiceUnavailableException
from src.system.repositories import SystemRepository
from src.system.services import HealthService, ReadinessService
from tests.fakes.db import FakeAsyncSession


class RedisOk:
    async def ping(self) -> bool:
        return True


class RedisFail:
    async def ping(self) -> bool:
        raise RuntimeError("down")


def build_readiness() -> ReadinessService:
    return ReadinessService(repository=SystemRepository())


def build_service(redis_client: object) -> HealthService:
    return HealthService(redis_client=redis_client, readiness=build_readiness())


@pytest.mark.asyncio
async def test_health_service_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()

    result = await build_service(RedisOk()).get_status(session=session)

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

    result = await build_service(RedisFail()).get_status(session=session)

    assert result.status == "degraded"
    assert result.postgres is True
    assert result.redis is False
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_health_service_reports_postgres_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/health/ must keep answering with a body exactly when a dependency dies."""
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))

    result = await build_service(RedisOk()).get_status(session=session)

    assert result.status == "degraded"
    assert result.postgres is False
    assert result.redis is True
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_ready_passes_on_live_database() -> None:
    await build_readiness().ensure_ready(FakeAsyncSession())


@pytest.mark.asyncio
async def test_ensure_ready_raises_on_dead_database() -> None:
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))

    with pytest.raises(ServiceUnavailableException):
        await build_readiness().ensure_ready(session)


@pytest.mark.asyncio
async def test_ensure_ready_raises_on_unresolvable_host() -> None:
    """A host that does not resolve reaches the probe as a bare socket error."""
    session = FakeAsyncSession()
    session.execute = AsyncMock(
        side_effect=socket.gaierror("Name or service not known")
    )

    with pytest.raises(ServiceUnavailableException):
        await build_readiness().ensure_ready(session)


@pytest.mark.asyncio
async def test_ensure_ready_raises_when_the_probe_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saturated connection pool must fail fast, not hang the probe."""
    monkeypatch.setattr("src.system.services.POSTGRES_PROBE_TIMEOUT_SECONDS", 0.01)
    session = FakeAsyncSession()

    async def never_answers(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(3600)

    session.execute = never_answers

    with pytest.raises(ServiceUnavailableException):
        await build_readiness().ensure_ready(session)
