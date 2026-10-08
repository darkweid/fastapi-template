from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.redis import lifecycle
from src.core.redis.dependencies import get_redis_client


@pytest.mark.asyncio
async def test_get_redis_client_returns_from_state() -> None:
    redis_client = object()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_client=redis_client))
    )

    resolved = await get_redis_client(request)  # type: ignore[arg-type]

    assert resolved is redis_client


@pytest.mark.asyncio
async def test_get_redis_client_missing_raises() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(RuntimeError):
        await get_redis_client(request)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_verify_redis_client_accepts_a_successful_ping() -> None:
    client = SimpleNamespace(ping=AsyncMock(return_value=True))

    await lifecycle.verify_redis_client(client)  # type: ignore[arg-type]

    client.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_redis_client_raises_when_ping_fails() -> None:
    """An unreachable Redis must fail startup, not every request after it."""
    client = SimpleNamespace(ping=AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="Redis ping failed"):
        await lifecycle.verify_redis_client(client)  # type: ignore[arg-type]
