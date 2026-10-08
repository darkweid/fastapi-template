import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from taskiq import InMemoryBroker

from taskiq_worker import receiver as receiver_module
from taskiq_worker.receiver import (
    RUNNING_CLAIM_TTL_SECONDS,
    IdempotencyReceiver,
    build_running_claim_key,
)

SHRUNK_TTL_SECONDS = 5


class EvalProbe:
    """Forwards to a client and signals once an EVAL has come back, so a test
    can stop the renewal loop after one renewal without sleeping."""

    def __init__(self, client: Redis) -> None:
        self._client = client
        self.evaluated = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def eval(self, *args: Any) -> Any:
        result = await self._client.eval(*args)
        self.evaluated.set()
        return result


def _receiver(client: Redis | EvalProbe) -> IdempotencyReceiver:
    receiver = IdempotencyReceiver(broker=InMemoryBroker(), run_startup=False)
    receiver._marker_client = cast(Redis, client)  # noqa: SLF001
    return receiver


async def _renew_once(
    receiver: IdempotencyReceiver, probe: EvalProbe, task_id: str, token: str
) -> None:
    renewal = asyncio.create_task(receiver._keep_claim(task_id, token))  # noqa: SLF001
    try:
        await asyncio.wait_for(probe.evaluated.wait(), timeout=5)
    finally:
        renewal.cancel()
        with suppress(asyncio.CancelledError):
            await renewal


async def test_a_claim_is_exclusive(redis_backend: Redis) -> None:
    """Two deliveries of one task in flight must not both run its side effect."""
    receiver = _receiver(redis_backend)
    task_id = uuid4().hex

    first = await receiver._claim(task_id)  # noqa: SLF001
    second = await receiver._claim(task_id)  # noqa: SLF001

    assert first is not None
    assert second is None
    ttl = await redis_backend.ttl(build_running_claim_key(task_id))
    assert 0 < ttl <= RUNNING_CLAIM_TTL_SECONDS


async def test_only_the_owner_releases_a_claim(redis_backend: Redis) -> None:
    """A run that outlived its claim must not delete the claim a reclaimed
    delivery took after it lapsed."""
    receiver = _receiver(redis_backend)
    task_id = uuid4().hex
    token = await receiver._claim(task_id)  # noqa: SLF001
    assert token is not None
    key = build_running_claim_key(task_id)

    await receiver._release_claim(task_id, "someone-else")  # noqa: SLF001
    assert await redis_backend.exists(key) == 1

    await receiver._release_claim(task_id, token)  # noqa: SLF001
    assert await redis_backend.exists(key) == 0


async def test_the_owner_renews_its_claim(
    redis_backend: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long task keeps its claim only because the heartbeat pushes the TTL back out."""
    monkeypatch.setattr(receiver_module, "CLAIM_RENEW_INTERVAL_SECONDS", 0)
    probe = EvalProbe(redis_backend)
    receiver = _receiver(probe)
    task_id = uuid4().hex
    token = await receiver._claim(task_id)  # noqa: SLF001
    assert token is not None
    key = build_running_claim_key(task_id)
    await redis_backend.expire(key, SHRUNK_TTL_SECONDS)

    await _renew_once(receiver, probe, task_id, token)

    assert await redis_backend.ttl(key) > SHRUNK_TTL_SECONDS


async def test_a_stranger_cannot_renew_a_claim(
    redis_backend: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A heartbeat left running by a crashed-and-reclaimed run must not keep
    someone else's claim alive."""
    monkeypatch.setattr(receiver_module, "CLAIM_RENEW_INTERVAL_SECONDS", 0)
    probe = EvalProbe(redis_backend)
    receiver = _receiver(probe)
    task_id = uuid4().hex
    assert await receiver._claim(task_id) is not None  # noqa: SLF001
    key = build_running_claim_key(task_id)
    await redis_backend.expire(key, SHRUNK_TTL_SECONDS)

    await _renew_once(receiver, probe, task_id, "someone-else")

    assert 0 < await redis_backend.ttl(key) <= SHRUNK_TTL_SECONDS


async def test_concurrent_claims_have_one_owner(
    real_redis: Redis, redis_client_factory: Callable[[], Redis]
) -> None:
    """Workers on separate connections racing for one task id: exactly one runs it."""
    task_id = uuid4().hex
    receivers = [_receiver(redis_client_factory()) for _ in range(8)]

    tokens = await asyncio.gather(
        *(receiver._claim(task_id) for receiver in receivers)  # noqa: SLF001
    )

    assert sum(token is not None for token in tokens) == 1
