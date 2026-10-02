from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.uow import ApplicationUnitOfWork, event_subscribers
from src.core.outbox.models import OutboxMessage
from src.event_log.actor import Actor
from src.event_log.enums import ObjectType
from src.event_log.events import DomainEvent, PublishedEvent
from src.event_log.models import EventLog

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class JournaledEvent(DomainEvent):
    code = "test.journaled"
    object_type = ObjectType.NOTE


class QuietEvent(DomainEvent):
    code = "test.quiet"
    object_type = ObjectType.NOTE
    journaled = False


async def test_the_journal_row_and_the_subscriber_share_the_occurrence(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[PublishedEvent, bool]] = []

    async def subscriber(uow: ApplicationUnitOfWork, published: PublishedEvent) -> None:
        row = await uow.session.get(EventLog, published.id)
        seen.append((published, row is not None))

    monkeypatch.setattr(event_subscribers, "_subscribers", [subscriber])
    object_id = uuid4()
    uow = ApplicationUnitOfWork(db_session)
    try:
        async with uow:
            published = await uow.publish(
                Actor.system(), JournaledEvent(object_id=object_id)
            )
            await uow.commit()

        assert seen == [(published, True)]
        row = await db_session.get(EventLog, published.id)
        assert row is not None and row.created_at == published.occurred_at
    finally:
        await db_session.execute(
            delete(EventLog).where(EventLog.object_id == object_id)
        )
        await db_session.commit()


async def test_an_unjournaled_event_writes_no_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(event_subscribers, "_subscribers", [])
    object_id = uuid4()
    uow = ApplicationUnitOfWork(db_session)
    async with uow:
        await uow.publish(Actor.system(), QuietEvent(object_id=object_id))
        await uow.commit()

    rows = await db_session.scalars(
        select(EventLog).where(EventLog.object_id == object_id)
    )
    assert rows.all() == []


async def test_rolled_back_savepoint_takes_the_subscriber_write_with_it(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of running subscribers in the publisher's transaction: an
    outbox row a subscriber wrote disappears with a rolled-back SAVEPOINT."""
    object_id = uuid4()
    task_name = f"test.subscriber_write.{object_id}"

    async def subscriber(uow: ApplicationUnitOfWork, published: PublishedEvent) -> None:
        uow.session.add(OutboxMessage(task_name=task_name, args=[str(published.id)]))

    monkeypatch.setattr(event_subscribers, "_subscribers", [subscriber])
    uow = ApplicationUnitOfWork(db_session)
    try:
        async with uow:
            savepoint = await db_session.begin_nested()
            await uow.publish(Actor.system(), JournaledEvent(object_id=object_id))
            pending = await db_session.scalars(
                select(OutboxMessage).where(OutboxMessage.task_name == task_name)
            )
            assert len(pending.all()) == 1
            await savepoint.rollback()
            await uow.commit()

        events = await db_session.scalars(
            select(EventLog).where(EventLog.object_id == object_id)
        )
        outbox = await db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.task_name == task_name)
        )
        assert events.all() == []
        assert outbox.all() == []
    finally:
        await db_session.execute(
            delete(OutboxMessage).where(OutboxMessage.task_name == task_name)
        )
        await db_session.execute(
            delete(EventLog).where(EventLog.object_id == object_id)
        )
        await db_session.commit()
