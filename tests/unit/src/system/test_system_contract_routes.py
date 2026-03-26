from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.core.database.session import get_session
from src.core.redis.dependencies import get_redis_client
from src.system import routers
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue

FIXED_TIME = datetime(2024, 1, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC"))


def fixed_utc_now() -> datetime:
    return FIXED_TIME


@pytest.mark.asyncio
async def test_system_routes_contract(
    async_client,
    dependency_overrides: DependencyOverrides,
    fake_session,
    fake_redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(get_redis_client, ProvideValue(fake_redis))
    monkeypatch.setattr(routers, "get_utc_now", fixed_utc_now)

    health_response = await async_client.get("/health/")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}

    time_response = await async_client.get("/time/")

    assert time_response.status_code == 200
    assert time_response.json() == {"time": "2024-01-01T12:00:00+00:00"}
