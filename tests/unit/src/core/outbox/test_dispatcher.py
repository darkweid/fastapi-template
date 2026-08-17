import enum
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from taskiq import InMemoryBroker

from src.core.outbox.dispatcher import TaskDispatcher
from tests.fakes.db import FakeSessionFactory, FakeUnitOfWork


def make_broker_and_probe() -> tuple[InMemoryBroker, list[tuple], object]:
    broker = InMemoryBroker(await_inplace=True)
    calls: list[tuple] = []

    @broker.task(task_name="outbox_probe")
    async def probe(value: str, flag: bool = False) -> None:
        calls.append((value, flag))

    return broker, calls, probe


def make_uow_with_outbox() -> tuple[FakeUnitOfWork, AsyncMock, MagicMock]:
    created_row = MagicMock()
    outbox_repo = AsyncMock()

    def _create(_session: object, data: dict) -> MagicMock:
        created_row.id = data["id"]
        created_row.task_name = data["task_name"]
        return created_row

    outbox_repo.create = AsyncMock(side_effect=_create)
    uow = FakeUnitOfWork(repositories={"outbox": outbox_repo})
    return uow, outbox_repo, created_row


async def test_enqueue_runs_task() -> None:
    _broker, calls, probe = make_broker_and_probe()
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())

    await dispatcher.enqueue(probe, "direct", flag=True)

    assert calls == [("direct", True)]


async def test_enqueue_transactional_inserts_row_and_defers_publish() -> None:
    _broker, calls, probe = make_broker_and_probe()
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())
    uow, outbox_repo, _row = make_uow_with_outbox()

    async with uow:
        await dispatcher.enqueue_transactional(uow, probe, "hello", flag=True)
        # Row inserted inside the transaction, task NOT executed yet.
        outbox_repo.create.assert_awaited_once()
        data = outbox_repo.create.await_args.args[1]
        assert data["task_name"] == "outbox_probe"
        assert data["args"] == ["hello"]
        assert data["kwargs"] == {"flag": True}
        assert isinstance(data["id"], UUID)
        assert data["id"].version == 7
        assert calls == []
        await uow.commit()

    # After-commit hook published and executed the task inline.
    assert calls == [("hello", True)]


async def test_publish_hook_marks_row_published() -> None:
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())
    dispatcher._outbox_repository = AsyncMock()  # noqa: SLF001
    uow, _outbox_repo, row = make_uow_with_outbox()

    kicker = MagicMock()
    kicker.with_task_id.return_value.kiq = AsyncMock()
    task = MagicMock()
    task.kicker.return_value = kicker

    async with uow:
        await dispatcher.enqueue_transactional(uow, task, "hello")
        await uow.commit()

    # The outbox row id doubles as the broker task_id: this is what lets the
    # IdempotencyReceiver dedupe a hook publish against a sweeper republish.
    kicker.with_task_id.assert_called_once_with(str(row.id))
    dispatcher._outbox_repository.mark_published.assert_awaited_once()  # noqa: SLF001
    assert (
        dispatcher._outbox_repository.mark_published.await_args.args[1] == row.id
    )  # noqa: SLF001


class _Color(enum.StrEnum):
    RED = "red"


@pytest.mark.parametrize(
    "bad_args,bad_kwargs",
    [
        ((uuid4(),), {}),  # not serializable at all
        ((float("nan"),), {}),  # dumps would emit the invalid-JSON token NaN
        ((), {"mapping": {1: "x"}}),  # int key coerced to "1" on republish
        (((1, 2),), {}),  # nested tuple becomes a list on republish
        ((_Color.RED,), {}),  # StrEnum == its str, but republish sends plain str
        ((), {"mapping": {_Color.RED: "x"}}),  # enum dict key decays the same way
    ],
)
async def test_enqueue_transactional_rejects_non_json_arguments(
    bad_args: tuple, bad_kwargs: dict
) -> None:
    # Arguments live in a JSONB outbox row: a UUID would raise deep inside the
    # INSERT flush, and coercible values would make the sweeper republish a
    # different payload - the guard fails at the call site and names the task.
    _broker, _calls, probe = make_broker_and_probe()
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())
    uow, outbox_repo, _row = make_uow_with_outbox()

    async with uow:
        with pytest.raises(TypeError, match="outbox_probe"):
            await dispatcher.enqueue_transactional(uow, probe, *bad_args, **bad_kwargs)
        outbox_repo.create.assert_not_awaited()
        await uow.rollback()


async def test_rollback_discards_publish() -> None:
    _broker, calls, probe = make_broker_and_probe()
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())
    uow, _outbox_repo, _row = make_uow_with_outbox()

    async with uow:
        await dispatcher.enqueue_transactional(uow, probe, "hello")
        await uow.rollback()

    assert calls == []


async def test_publish_failure_does_not_break_commit() -> None:
    _broker, _calls, probe = make_broker_and_probe()
    dispatcher = TaskDispatcher(session_factory=FakeSessionFactory())
    uow, _outbox_repo, _row = make_uow_with_outbox()
    broken = AsyncMock(side_effect=RuntimeError("redis down"))
    # Simulate kiq failure BEFORE hook creation so partial captures the broken method.
    dispatcher._publish = broken  # noqa: SLF001

    async with uow:
        await dispatcher.enqueue_transactional(uow, probe, "hello")
        await uow.commit()  # must not raise

    assert uow.completed
    # Verify the failing hook was actually invoked (not captured by dispatcher).
    broken.assert_awaited_once()
