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
    def __init__(self, used_memory: int = 100, maxmemory: int = 1000) -> None:
        self.memory = {"used_memory": used_memory, "maxmemory": maxmemory}

    async def ping(self) -> bool:
        return True

    async def info(self, section: str) -> dict[str, int]:
        assert section == "memory"
        return self.memory


class RedisFail:
    async def ping(self) -> bool:
        raise RuntimeError("down")


def build_readiness(session: FakeAsyncSession) -> ReadinessService:
    return ReadinessService(repository=SystemRepository(), session=session)


def build_service(redis_client: object, session: FakeAsyncSession) -> HealthService:
    return HealthService(redis_client=redis_client, readiness=build_readiness(session))


@pytest.mark.asyncio
async def test_health_service_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    sentry_mock = Mock()
    monkeypatch.setattr("sentry_sdk.capture_exception", sentry_mock)
    session = FakeAsyncSession()

    result = await build_service(RedisOk(), session).get_status()

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

    result = await build_service(RedisFail(), session).get_status()

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

    result = await build_service(RedisOk(), session).get_status()

    assert result.status == "degraded"
    assert result.postgres is False
    assert result.redis is True
    sentry_mock.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_ready_passes_on_live_database() -> None:
    await build_readiness(FakeAsyncSession()).ensure_ready()


@pytest.mark.asyncio
async def test_ensure_ready_raises_on_dead_database() -> None:
    session = FakeAsyncSession()
    session.execute = AsyncMock(side_effect=SQLAlchemyError("fail"))

    with pytest.raises(ServiceUnavailableException):
        await build_readiness(session).ensure_ready()


@pytest.mark.asyncio
async def test_ensure_ready_raises_on_unresolvable_host() -> None:
    """A host that does not resolve reaches the probe as a bare socket error."""
    session = FakeAsyncSession()
    session.execute = AsyncMock(
        side_effect=socket.gaierror("Name or service not known")
    )

    with pytest.raises(ServiceUnavailableException):
        await build_readiness(session).ensure_ready()


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
        await build_readiness(session).ensure_ready()


@pytest.mark.asyncio
async def test_health_reports_redis_memory_use() -> None:
    result = await build_service(RedisOk(250, 1000), FakeAsyncSession()).get_status()

    assert result.status == "ok"
    assert result.redis_memory_used_ratio == 0.25


@pytest.mark.asyncio
async def test_health_degrades_when_redis_nears_its_memory_cap() -> None:
    """Under noeviction a full Redis refuses every write - sessions, OTPs, task
    enqueues - while PING still answers, so the ping alone reports "ok"."""
    result = await build_service(RedisOk(950, 1000), FakeAsyncSession()).get_status()

    assert result.status == "degraded"
    assert result.redis is True
    assert result.redis_memory_used_ratio == 0.95


@pytest.mark.asyncio
async def test_health_reports_no_ratio_without_a_memory_cap() -> None:
    result = await build_service(RedisOk(950, 0), FakeAsyncSession()).get_status()

    assert result.status == "ok"
    assert result.redis_memory_used_ratio is None
