from unittest.mock import AsyncMock, MagicMock, patch

from taskiq import InMemoryBroker, TaskiqMessage
from taskiq.receiver import Receiver
from taskiq.result import TaskiqResult

from taskiq_worker.receiver import (
    IDEMPOTENCY_MARKER_TTL_SECONDS,
    IdempotencyReceiver,
    build_idempotency_marker_key,
)


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
    client = AsyncMock()
    client.get = AsyncMock(return_value="1")
    receiver = make_receiver(client)

    with patch.object(Receiver, "run_task", new=AsyncMock()) as super_run:
        result = await receiver.run_task(MagicMock(), make_message())

    super_run.assert_not_awaited()
    assert result.is_err is False
    client.set.assert_not_awaited()


async def test_success_sets_marker_with_ttl() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    receiver = make_receiver(client)

    with patch.object(
        Receiver, "run_task", new=AsyncMock(return_value=success_result())
    ):
        await receiver.run_task(MagicMock(), make_message(task_id="abc"))

    client.set.assert_awaited_once_with(
        build_idempotency_marker_key("abc"), "1", ex=IDEMPOTENCY_MARKER_TTL_SECONDS
    )


async def test_error_result_sets_no_marker() -> None:
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    receiver = make_receiver(client)

    with patch.object(Receiver, "run_task", new=AsyncMock(return_value=error_result())):
        result = await receiver.run_task(MagicMock(), make_message())

    assert result.is_err is True
    client.set.assert_not_awaited()


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
