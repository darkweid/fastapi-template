import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
from taskiq import InMemoryBroker, TaskiqMessage
from taskiq.acks import AckController
from taskiq.receiver import Receiver
from taskiq.result import TaskiqResult

from taskiq_worker import receiver as receiver_module
from taskiq_worker.broker import STREAM_IDLE_TIMEOUT_SECONDS
from taskiq_worker.receiver import (
    CLAIM_RENEW_INTERVAL_SECONDS,
    IDEMPOTENCY_MARKER_TTL_SECONDS,
    RELEASE_CLAIM_SCRIPT,
    RUNNING_CLAIM_TTL_SECONDS,
    IdempotencyReceiver,
    build_idempotency_marker_key,
    build_running_claim_key,
)
from tests.fakes.redis import InMemoryRedis


def make_receiver(marker_client: AsyncMock) -> IdempotencyReceiver:
    receiver = IdempotencyReceiver(broker=InMemoryBroker(), run_startup=False)
    receiver._marker_client = marker_client  # noqa: SLF001
    return receiver


def make_message(task_id: str = "row-uuid") -> TaskiqMessage:
    return TaskiqMessage(
        task_id=task_id, task_name="probe", labels={}, args=[], kwargs={}
    )


def success_result() -> TaskiqResult:
    return TaskiqResult(is_err=False, return_value=None, execution_time=0.0)


def error_result() -> TaskiqResult:
    return TaskiqResult(
        is_err=True, return_value=None, execution_time=0.0, error=RuntimeError("x")
    )


async def test_marker_present_skips_execution() -> None:
    client = InMemoryRedis()
    await client.set(build_idempotency_marker_key("row-uuid"), "1")
    receiver = make_receiver(client)  # type: ignore[arg-type]

    with patch.object(Receiver, "run_task", new=AsyncMock()) as super_run:
        result = await receiver.run_task(MagicMock(), make_message())

    super_run.assert_not_awaited()
    assert result.is_err is False
    # The claim taken to read the marker safely is given back.
    assert await client.exists(build_running_claim_key("row-uuid")) == 0


class OriginalFinishesBeforeTheClaim(InMemoryRedis):
    """The first delivery completes - marker written, claim released - while the
    duplicate is on its way to take the claim."""

    async def set(self, key: str | bytes, value: object, **kwargs: object) -> bool:
        if kwargs.get("nx") and not getattr(self, "_finished", False):
            self._finished = True
            await super().set(build_idempotency_marker_key("row-uuid"), "1")
            await super().delete(build_running_claim_key("row-uuid"))
        return await super().set(key, value, **kwargs)  # type: ignore[arg-type]


async def test_a_run_finishing_just_before_the_claim_is_not_repeated() -> None:
    """Reading the marker before claiming would miss one written in between and
    then find the claim free, so the task would run a second time."""
    receiver = make_receiver(OriginalFinishesBeforeTheClaim())  # type: ignore[arg-type]

    with patch.object(Receiver, "run_task", new=AsyncMock()) as super_run:
        await receiver.run_task(MagicMock(), make_message())

    super_run.assert_not_awaited()


async def test_success_sets_marker_with_ttl() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    receiver = make_receiver(client)

    with patch.object(
        Receiver, "run_task", new=AsyncMock(return_value=success_result())
    ):
        await receiver.run_task(MagicMock(), make_message(task_id="abc"))

    client.set.assert_has_awaits(
        [
            call(
                build_running_claim_key("abc"),
                ANY,
                ex=RUNNING_CLAIM_TTL_SECONDS,
                nx=True,
            ),
            call(
                build_idempotency_marker_key("abc"),
                "1",
                ex=IDEMPOTENCY_MARKER_TTL_SECONDS,
            ),
        ]
    )
    token = client.set.await_args_list[0].args[1]
    client.eval.assert_awaited_once_with(
        RELEASE_CLAIM_SCRIPT, 1, build_running_claim_key("abc"), token
    )


async def test_error_result_sets_no_marker() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    receiver = make_receiver(client)

    with patch.object(Receiver, "run_task", new=AsyncMock(return_value=error_result())):
        result = await receiver.run_task(MagicMock(), make_message(task_id="abc"))

    assert result.is_err is True
    marker_key = build_idempotency_marker_key("abc")
    assert all(awaited.args[0] != marker_key for awaited in client.set.await_args_list)
    # Released, so a retry of the failed run is not taken for a duplicate.
    token = client.set.await_args_list[0].args[1]
    client.eval.assert_awaited_once_with(
        RELEASE_CLAIM_SCRIPT, 1, build_running_claim_key("abc"), token
    )


async def test_redis_get_failure_fails_open() -> None:
    client = AsyncMock()
    client.get = AsyncMock(side_effect=ConnectionError("redis down"))
    receiver = make_receiver(client)

    with patch.object(
        Receiver, "run_task", new=AsyncMock(return_value=success_result())
    ) as super_run:
        result = await receiver.run_task(MagicMock(), make_message())

    super_run.assert_awaited_once()
    assert result.is_err is False


async def test_redis_set_failure_does_not_break_result() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(side_effect=ConnectionError("redis down"))
    receiver = make_receiver(client)

    with patch.object(
        Receiver, "run_task", new=AsyncMock(return_value=success_result())
    ):
        result = await receiver.run_task(MagicMock(), make_message())

    assert result.is_err is False


async def test_a_concurrent_second_delivery_is_skipped() -> None:
    """The outbox sweeper can republish a row whose after-commit publish already
    went out; both deliveries carry the row id and must not both run."""
    release = asyncio.Event()
    runs = 0

    async def slow_run(*_args: object, **_kwargs: object) -> TaskiqResult:
        nonlocal runs
        runs += 1
        await release.wait()
        return success_result()

    receiver = make_receiver(InMemoryRedis())  # type: ignore[arg-type]

    with patch.object(Receiver, "run_task", new=slow_run):
        first = asyncio.create_task(receiver.run_task(MagicMock(), make_message()))
        await asyncio.sleep(0.01)
        second = await receiver.run_task(MagicMock(), make_message())
        release.set()
        await first

    assert runs == 1
    assert second.is_err is False


async def test_a_redelivery_after_a_failed_run_executes_again() -> None:
    client = InMemoryRedis()
    receiver = make_receiver(client)  # type: ignore[arg-type]

    with patch.object(
        Receiver,
        "run_task",
        new=AsyncMock(side_effect=[error_result(), success_result()]),
    ) as super_run:
        await receiver.run_task(MagicMock(), make_message())
        await receiver.run_task(MagicMock(), make_message())

    assert super_run.await_count == 2


def test_the_claim_is_renewed_in_time_and_lapses_before_the_reclaim() -> None:
    """A renewal slower than the TTL lets a second delivery run beside a live one;
    a claim outliving the reclaim timeout makes a crashed worker's message be
    skipped and acked - the task lost rather than duplicated."""
    assert CLAIM_RENEW_INTERVAL_SECONDS * 2 < RUNNING_CLAIM_TTL_SECONDS
    assert RUNNING_CLAIM_TTL_SECONDS < STREAM_IDLE_TIMEOUT_SECONDS


async def test_a_long_run_keeps_renewing_its_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task may run for longer than one TTL; without renewal its claim would
    lapse mid-run and let a duplicate delivery start beside it."""
    monkeypatch.setattr(receiver_module, "CLAIM_RENEW_INTERVAL_SECONDS", 0.01)
    client = InMemoryRedis()
    receiver = make_receiver(client)  # type: ignore[arg-type]
    claim_key = build_running_claim_key("row-uuid")
    renewals: list[int] = []

    async def long_run(*_args: object, **_kwargs: object) -> TaskiqResult:
        await client.expire(claim_key, 1)
        await asyncio.sleep(0.05)
        renewals.append(await client.ttl(claim_key))
        return success_result()

    with patch.object(Receiver, "run_task", new=long_run):
        await receiver.run_task(MagicMock(), make_message())

    assert renewals == [RUNNING_CLAIM_TTL_SECONDS]
    assert await client.exists(claim_key) == 0


async def test_a_late_renewal_does_not_extend_a_successor_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(receiver_module, "CLAIM_RENEW_INTERVAL_SECONDS", 0.01)
    client = InMemoryRedis()
    receiver = make_receiver(client)  # type: ignore[arg-type]
    claim_key = build_running_claim_key("row-uuid")

    async def lose_the_claim(*_args: object, **_kwargs: object) -> TaskiqResult:
        await client.set(claim_key, "successor-token", ex=5)
        await asyncio.sleep(0.05)
        return error_result()

    with patch.object(Receiver, "run_task", new=lose_the_claim):
        await receiver.run_task(MagicMock(), make_message())

    assert await client.get(claim_key) == "successor-token"
    assert await client.ttl(claim_key) == 5


async def test_a_delivery_that_finds_a_live_claim_is_left_unacked() -> None:
    """It may be the stream reclaiming the entry the claim holder still runs;
    acked, the entry would be lost if that worker then crashed."""
    client = InMemoryRedis()
    await client.set(build_running_claim_key("row-uuid"), "holder-token", ex=60)
    receiver = make_receiver(client)  # type: ignore[arg-type]
    ack = AsyncMock()
    ack_controller = AckController(ack)

    with patch.object(Receiver, "run_task", new=AsyncMock()) as super_run:
        await receiver.run_task(MagicMock(), make_message(), ack_controller)
    await ack_controller.ack()

    super_run.assert_not_awaited()
    ack.assert_not_awaited()


async def test_a_delivery_of_a_completed_task_is_acked() -> None:
    client = InMemoryRedis()
    await client.set(build_idempotency_marker_key("row-uuid"), "1")
    receiver = make_receiver(client)  # type: ignore[arg-type]
    ack = AsyncMock()
    ack_controller = AckController(ack)

    await receiver.run_task(MagicMock(), make_message(), ack_controller)
    await ack_controller.ack()

    ack.assert_awaited_once()
