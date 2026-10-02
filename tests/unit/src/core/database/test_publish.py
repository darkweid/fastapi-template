from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.core.database.uow import ApplicationUnitOfWork, event_subscribers
from src.event_log.actor import Actor
from src.event_log.enums import ObjectType
from src.event_log.events import DomainEvent, PublishedEvent
from src.event_log.models import EventLog


class JournaledEvent(DomainEvent):
    code = "test.journaled"
    object_type = ObjectType.NOTE


class QuietEvent(DomainEvent):
    code = "test.quiet"
    object_type = ObjectType.NOTE
    journaled = False


def _session(*, insert_fails: bool = False) -> MagicMock:
    session = MagicMock()
    session.flush = AsyncMock()
    savepoint = MagicMock()
    savepoint.__aenter__ = AsyncMock(
        side_effect=SQLAlchemyError("boom") if insert_fails else None
    )
    savepoint.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=savepoint)
    return session


def _added(session: MagicMock) -> list[EventLog]:
    return [call.args[0] for call in session.add.call_args_list]


def test_a_subscriber_is_registered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both entry points register; a second call must not send twice."""
    monkeypatch.setattr(event_subscribers, "_subscribers", [])
    subscriber = AsyncMock()

    event_subscribers.subscribe_to_events(subscriber)
    event_subscribers.subscribe_to_events(subscriber)

    assert event_subscribers.event_subscribers() == (subscriber,)


async def test_every_subscriber_gets_the_journaled_occurrence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = AsyncMock(), AsyncMock()
    monkeypatch.setattr(event_subscribers, "_subscribers", [first, second])
    session = _session()
    uow = ApplicationUnitOfWork(session)
    actor, event = Actor.system(), JournaledEvent(object_id=uuid4())

    await uow.publish(actor, event)

    ((row,),) = [_added(session)]
    (called_uow, published), _ = first.await_args
    assert called_uow is uow and isinstance(published, PublishedEvent)
    assert (published.actor, published.event) == (actor, event)
    # One id and one instant for the journal and every subscriber.
    assert (row.id, row.created_at) == (published.id, published.occurred_at)
    second.assert_awaited_once_with(uow, published)


async def test_a_failed_journal_row_does_not_stop_the_subscribers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this design removes: a swallowed audit failure used to
    drop the notification with it."""
    monkeypatch.setattr("src.event_log.repositories.sentry_sdk", MagicMock())
    subscriber = AsyncMock()
    monkeypatch.setattr(event_subscribers, "_subscribers", [subscriber])
    uow = ApplicationUnitOfWork(_session(insert_fails=True))

    await uow.publish(Actor.system(), JournaledEvent(object_id=uuid4()))

    subscriber.assert_awaited_once()


async def test_an_unjournaled_event_reaches_subscribers_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscriber = AsyncMock()
    monkeypatch.setattr(event_subscribers, "_subscribers", [subscriber])
    session = _session()
    uow = ApplicationUnitOfWork(session)

    await uow.publish(Actor.system(), QuietEvent(object_id=uuid4()))

    assert _added(session) == []
    subscriber.assert_awaited_once()


async def test_a_subscriber_error_is_the_publishers_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a subscriber writes (an outbox row) is part of the action."""
    monkeypatch.setattr(
        event_subscribers,
        "_subscribers",
        [AsyncMock(side_effect=RuntimeError("outbox down"))],
    )
    uow = ApplicationUnitOfWork(_session())

    with pytest.raises(RuntimeError, match="outbox down"):
        await uow.publish(Actor.system(), JournaledEvent(object_id=uuid4()))
