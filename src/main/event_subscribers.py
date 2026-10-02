"""Every subscriber to published domain events, in one list. The entry points
call `register_event_subscribers` at import: a subscriber missing here never
runs, in the API or in the worker.

A subscriber is `async (uow, published) -> None` (`EventSubscriber` in
`src/core/database/uow/event_subscribers.py`): a module that reacts to an
event (a notification, an integration) adds its function here instead of
reading the journal."""

from src.core.database.uow.event_subscribers import EventSubscriber, subscribe_to_events

EVENT_SUBSCRIBERS: tuple[EventSubscriber, ...] = ()


def register_event_subscribers() -> None:
    for subscriber in EVENT_SUBSCRIBERS:
        subscribe_to_events(subscriber)
