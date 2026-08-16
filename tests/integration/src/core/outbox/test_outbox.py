"""The transactional outbox against a real transaction.

The whole point of `enqueue_transactional` is that the outbox row shares the caller's
transaction: it survives exactly when the business data survives. That is a database
guarantee, so a fake session can only assert that a method was called, never that the row
and the commit are actually tied together.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from taskiq import InMemoryBroker

from src.core.database.uow.application import ApplicationUnitOfWork
from src.core.outbox.dispatcher import TaskDispatcher
from src.core.outbox.enums import OutboxMessageStatus
from src.core.outbox.models import OutboxMessage
from src.core.outbox.repositories import OutboxRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

TASK_NAME = "integration_outbox_probe"

# A broker of its own, so registering the probe cannot disturb the application broker
# other tests import. `await_inplace` never comes into play: `publish_spy` replaces the
# kicker before anything is kicked.
probe_broker = InMemoryBroker(await_inplace=True)


@probe_broker.task(task_name=TASK_NAME)
async def probe_task(value: str, flag: bool = False) -> None:
    """Placeholder body: the tests assert on the publish call, never on execution."""


@pytest.fixture
def publish_spy(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace the probe's kicker so the publish is observed instead of performed.

    `TaskDispatcher._publish` goes through `task.kicker().with_task_id(...).kiq(...)` —
    the task id it sets is the outbox row id, which is what makes a republished message
    deduplicate worker-side, so the spy has to sit behind `with_task_id` to see it.
    """
    kiq = AsyncMock()
    kicker = MagicMock()
    kicker.with_task_id.return_value.kiq = kiq
    monkeypatch.setattr(probe_task, "kicker", MagicMock(return_value=kicker))
    return kiq


@pytest.fixture
def dispatcher(integration_engine: AsyncEngine) -> TaskDispatcher:
    return TaskDispatcher(
        session_factory=async_sessionmaker(integration_engine, expire_on_commit=False)
    )


@pytest_asyncio.fixture(loop_scope="session")
async def committed_message_ids(
    integration_engine: AsyncEngine,
) -> AsyncGenerator[list[UUID]]:
    """Outbox rows a test committed on purpose, removed afterwards."""
    message_ids: list[UUID] = []
    yield message_ids
    if message_ids:
        async with AsyncSession(integration_engine) as session, session.begin():
            await session.execute(
                delete(OutboxMessage).where(OutboxMessage.id.in_(message_ids))
            )


async def read_message(engine: AsyncEngine, message_id: UUID) -> OutboxMessage | None:
    async with AsyncSession(engine) as session:
        return await OutboxRepository().get_single(session, id=message_id)


async def enqueued_id(uow: ApplicationUnitOfWork) -> UUID:
    """Id of the row the UoW's outbox repository just staged.

    The read flushes the staged INSERT, which is also what makes it visible to the
    surrounding transaction. `get_list` returns newest first, so a row left behind by an
    earlier failed test cannot be mistaken for this one.
    """
    rows = await uow.outbox.get_list(uow.session, task_name=TASK_NAME)
    assert rows
    return rows[0].id


async def test_commit_persists_the_row_and_publishes_it(
    integration_engine: AsyncEngine,
    dispatcher: TaskDispatcher,
    publish_spy: AsyncMock,
    committed_message_ids: list[UUID],
) -> None:
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork = ApplicationUnitOfWork(session)
        async with uow:
            await dispatcher.enqueue_transactional(uow, probe_task, "hello", flag=True)
            message_id = await enqueued_id(uow)
            committed_message_ids.append(message_id)
            # Still inside the transaction: nothing may have been published yet.
            publish_spy.assert_not_awaited()
            await uow.commit()

    publish_spy.assert_awaited_once_with("hello", flag=True)
    probe_task.kicker.return_value.with_task_id.assert_called_once_with(str(message_id))

    persisted = await read_message(integration_engine, message_id)
    assert persisted is not None
    assert persisted.task_name == TASK_NAME
    assert persisted.args == ["hello"]
    assert persisted.kwargs == {"flag": True}
    # The after-commit hook marks the row published through its own session.
    assert persisted.status is OutboxMessageStatus.PUBLISHED
    assert persisted.published_at is not None


async def test_rollback_discards_the_row_and_publishes_nothing(
    integration_engine: AsyncEngine,
    dispatcher: TaskDispatcher,
    publish_spy: AsyncMock,
) -> None:
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork = ApplicationUnitOfWork(session)
        async with uow:
            await dispatcher.enqueue_transactional(uow, probe_task, "dropped")
            message_id = await enqueued_id(uow)
            await uow.rollback()

    publish_spy.assert_not_awaited()
    assert await read_message(integration_engine, message_id) is None


async def test_pending_row_is_the_sweeper_batch_after_a_failed_publish(
    integration_engine: AsyncEngine,
    dispatcher: TaskDispatcher,
    publish_spy: AsyncMock,
    committed_message_ids: list[UUID],
) -> None:
    """A publish that raises must leave the row PENDING for the sweeper to retry.

    The hook swallows its exception on purpose — the data is already committed — so the
    only evidence that delivery is still owed is the row's own state.
    """
    publish_spy.side_effect = RuntimeError("broker unreachable")

    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork = ApplicationUnitOfWork(session)
        async with uow:
            await dispatcher.enqueue_transactional(uow, probe_task, "retry-me")
            message_id = await enqueued_id(uow)
            committed_message_ids.append(message_id)
            await uow.commit()

    async with AsyncSession(integration_engine) as session, session.begin():
        batch = await OutboxRepository().get_batch_for_publish(session, limit=10)
        pending_ids = {message.id for message in batch}

    assert message_id in pending_ids
    persisted = await read_message(integration_engine, message_id)
    assert persisted is not None
    assert persisted.status is OutboxMessageStatus.PENDING
