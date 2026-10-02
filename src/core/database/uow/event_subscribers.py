"""Subscribers to the domain events a unit of work publishes.

`src/main/event_subscribers.py` is the one place that lists them; the entry
points (`src/main/web.py`, `taskiq_worker/app.py`) call it at import. A
subscriber runs on the publisher's session inside its transaction, so what it
writes commits or rolls back with the action, and its failure is the action's
failure. The journal is not a subscriber: `ApplicationUnitOfWork.publish`
writes it itself, best-effort, before any of them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.database.uow.application import ApplicationUnitOfWork
    from src.event_log.events import PublishedEvent

EventSubscriber = Callable[["ApplicationUnitOfWork", "PublishedEvent"], Awaitable[None]]

_subscribers: list[EventSubscriber] = []


def subscribe_to_events(subscriber: EventSubscriber) -> None:
    if subscriber not in _subscribers:
        _subscribers.append(subscriber)


def event_subscribers() -> tuple[EventSubscriber, ...]:
    return tuple(_subscribers)
