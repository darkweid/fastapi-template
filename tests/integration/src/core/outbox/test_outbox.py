"""The transactional outbox against a real transaction.

The whole point of `enqueue_transactional` is that the outbox row shares the caller's
transaction: it survives exactly when the business data survives. That is a database
guarantee, so a fake session can only assert that a method was called, never that the row
and the commit are actually tied together.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from taskiq import InMemoryBroker

from src.core.database.transactions import set_local_statement_timeout
from src.core.database.uow.application import ApplicationUnitOfWork
from src.core.outbox import tasks as outbox_tasks
from src.core.outbox.dispatcher import TaskDispatcher
from src.core.outbox.enums import OutboxMessageStatus
from src.core.outbox.models import OutboxMessage
from src.core.outbox.repositories import OutboxRepository
from src.core.outbox.tasks import SWEEPER_GRACE
from src.core.utils.datetime_utils import get_utc_now

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


async def backdate(engine: AsyncEngine, message_id: UUID) -> None:
    """Move a row's creation past the sweeper grace on the database clock."""
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message_id)
            .values(created_at=func.now() - SWEEPER_GRACE - timedelta(seconds=1))
        )


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

    await backdate(integration_engine, message_id)
    async with AsyncSession(integration_engine) as session, session.begin():
        batch = await OutboxRepository().get_batch_for_publish(
            session, limit=10, min_age=SWEEPER_GRACE
        )
        pending_ids = {message.id for message in batch}

    assert message_id in pending_ids
    persisted = await read_message(integration_engine, message_id)
    assert persisted is not None
    assert persisted.status is OutboxMessageStatus.PENDING


async def test_the_sweeper_leaves_a_row_younger_than_the_grace_to_its_publish(
    integration_engine: AsyncEngine,
    dispatcher: TaskDispatcher,
    publish_spy: AsyncMock,
    committed_message_ids: list[UUID],
) -> None:
    """The after-commit publish kiqs first and marks the row published after; a
    sweep in between would take the row still PENDING and publish it twice."""
    publish_spy.side_effect = RuntimeError("broker unreachable")

    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork = ApplicationUnitOfWork(session)
        async with uow:
            await dispatcher.enqueue_transactional(uow, probe_task, "fresh")
            message_id = await enqueued_id(uow)
            committed_message_ids.append(message_id)
            await uow.commit()

    async with AsyncSession(integration_engine) as session, session.begin():
        batch = await OutboxRepository().get_batch_for_publish(
            session, limit=100, min_age=SWEEPER_GRACE
        )

    assert message_id not in {message.id for message in batch}


async def test_a_row_is_stamped_at_its_insert_not_at_its_transaction_start(
    integration_engine: AsyncEngine,
    dispatcher: TaskDispatcher,
) -> None:
    """now() is frozen at the start of the enqueuing transaction; aged by it, a
    row from a transaction that stayed open past the grace is fair game for the
    sweeper the moment it commits, while its own publish is still running."""
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork = ApplicationUnitOfWork(session)
        async with uow:
            before_insert = await uow.session.scalar(select(func.clock_timestamp()))
            await dispatcher.enqueue_transactional(uow, probe_task, "late")
            message_id = await enqueued_id(uow)
            created_at = await uow.session.scalar(
                select(OutboxMessage.created_at).where(OutboxMessage.id == message_id)
            )
            await uow.rollback()

    assert created_at > before_insert


MakeMessages = Callable[..., Awaitable[list[UUID]]]


@pytest_asyncio.fixture(loop_scope="session")
async def make_messages(
    integration_engine: AsyncEngine, committed_message_ids: list[UUID]
) -> MakeMessages:
    """Commit `count` rows of one status, each `age` old on the database clock."""

    async def make(
        count: int, status: OutboxMessageStatus, age: timedelta
    ) -> list[UUID]:
        stamp = func.now() - age
        messages = [
            OutboxMessage(
                task_name=TASK_NAME,
                status=status,
                created_at=stamp,
                updated_at=stamp,
                published_at=(
                    stamp if status is OutboxMessageStatus.PUBLISHED else None
                ),
            )
            for _ in range(count)
        ]
        async with AsyncSession(integration_engine) as session, session.begin():
            session.add_all(messages)
            await session.flush()
            ids = [message.id for message in messages]
        committed_message_ids.extend(ids)
        return ids

    return make


async def surviving(engine: AsyncEngine, ids: list[UUID]) -> set[UUID]:
    async with AsyncSession(engine) as session:
        rows = await session.scalars(
            select(OutboxMessage.id).where(OutboxMessage.id.in_(ids))
        )
        return set(rows)


# Older than any row another test leaves behind, so a purge that may take only a
# few rows is asked about these alone.
PURGE_AGE = timedelta(days=400)
PURGE_CUTOFF = timedelta(days=365)


async def test_concurrent_sweeps_take_disjoint_batches(
    integration_engine: AsyncEngine, make_messages: MakeMessages
) -> None:
    """A second sweep that waited for the first would publish the same rows; one
    that skips what is locked takes the next ones at once."""
    ids = set(await make_messages(4, OutboxMessageStatus.PENDING, PURGE_AGE))
    repository = OutboxRepository()

    async with (
        AsyncSession(integration_engine) as first,
        AsyncSession(integration_engine) as second,
    ):
        taken = await repository.get_batch_for_publish(
            first, limit=2, min_age=PURGE_CUTOFF
        )
        # Without SKIP LOCKED this waits for `first` until the timeout ends it.
        await second.begin()
        await set_local_statement_timeout(second, 5)
        next_taken = await repository.get_batch_for_publish(
            second, limit=4, min_age=PURGE_CUTOFF
        )
        first_ids = {message.id for message in taken}
        second_ids = {message.id for message in next_taken}

    assert len(first_ids) == 2
    assert first_ids < ids
    assert second_ids == ids - first_ids


@pytest.mark.parametrize(
    "status", [OutboxMessageStatus.PUBLISHED, OutboxMessageStatus.FAILED]
)
async def test_a_purge_deletes_only_old_rows_of_its_status(
    integration_engine: AsyncEngine,
    make_messages: MakeMessages,
    status: OutboxMessageStatus,
) -> None:
    old = await make_messages(2, status, PURGE_AGE)
    kept = [
        *await make_messages(1, status, PURGE_CUTOFF - timedelta(days=1)),
        *await make_messages(1, OutboxMessageStatus.PENDING, PURGE_AGE),
        *await make_messages(
            1,
            (
                OutboxMessageStatus.FAILED
                if status is OutboxMessageStatus.PUBLISHED
                else OutboxMessageStatus.PUBLISHED
            ),
            PURGE_AGE,
        ),
    ]

    async with AsyncSession(integration_engine) as session, session.begin():
        deleted = await OutboxRepository().purge(
            session, status, get_utc_now() - PURGE_CUTOFF, limit=100
        )

    assert deleted == 2
    assert await surviving(integration_engine, old + kept) == set(kept)


async def test_a_purge_never_deletes_more_than_its_limit(
    integration_engine: AsyncEngine, make_messages: MakeMessages
) -> None:
    ids = await make_messages(5, OutboxMessageStatus.PUBLISHED, PURGE_AGE)

    async with AsyncSession(integration_engine) as session, session.begin():
        deleted = await OutboxRepository().purge(
            session,
            OutboxMessageStatus.PUBLISHED,
            get_utc_now() - PURGE_CUTOFF,
            limit=2,
        )

    assert deleted == 2
    assert len(await surviving(integration_engine, ids)) == 3


async def test_a_purge_skips_a_row_another_transaction_holds(
    integration_engine: AsyncEngine, make_messages: MakeMessages
) -> None:
    """A purge that waited would hold its own batch locked for as long."""
    held, free = await make_messages(2, OutboxMessageStatus.FAILED, PURGE_AGE)

    async with (
        AsyncSession(integration_engine) as holder,
        AsyncSession(integration_engine) as purging,
    ):
        await OutboxRepository().get_single(holder, id=held, for_update=True)
        await purging.begin()
        await set_local_statement_timeout(purging, 5)
        deleted = await OutboxRepository().purge(
            purging,
            OutboxMessageStatus.FAILED,
            get_utc_now() - PURGE_CUTOFF,
            limit=100,
        )
        await purging.commit()
        await holder.rollback()

    assert deleted == 1
    assert await surviving(integration_engine, [held, free]) == {held}


async def test_the_purge_task_drains_a_backlog_batch_by_batch(
    integration_engine: AsyncEngine,
    make_messages: MakeMessages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the real unit of work, which refuses a second use: a loop inside one
    would pass over the fake and fail here."""
    monkeypatch.setattr(outbox_tasks, "PURGE_BATCH_SIZE", 2)
    ids = [
        *await make_messages(5, OutboxMessageStatus.PUBLISHED, PURGE_AGE),
        *await make_messages(3, OutboxMessageStatus.FAILED, PURGE_AGE),
    ]
    kept = [
        *await make_messages(
            1,
            OutboxMessageStatus.PUBLISHED,
            timedelta(days=outbox_tasks.PUBLISHED_RETENTION_DAYS - 1),
        ),
        # Past the published retention and inside its own.
        *await make_messages(
            1,
            OutboxMessageStatus.FAILED,
            timedelta(days=outbox_tasks.FAILED_RETENTION_DAYS - 1),
        ),
    ]

    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        await outbox_tasks.outbox_purge(session=session)

    assert await surviving(integration_engine, ids + kept) == set(kept)


async def test_a_purge_can_read_through_its_index(
    integration_engine: AsyncEngine,
) -> None:
    """A partial index serves only a query whose WHERE implies its predicate; with
    sequential scans off the planner uses it whenever it can."""
    statements = {
        "ix_outbox_messages_published_at": "status = 'PUBLISHED' AND published_at",
        "ix_outbox_messages_updated_at": "status = 'FAILED' AND updated_at",
    }
    async with integration_engine.connect() as connection:
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        for index, condition in statements.items():
            plan = await connection.scalars(
                text(
                    f"EXPLAIN SELECT id FROM outbox_messages WHERE {condition} < now()"
                )
            )
            assert index in "\n".join(plan)
        await connection.rollback()
