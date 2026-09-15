from __future__ import annotations

from typing import Any

from src.event_log.actor import Actor
from src.event_log.events import DomainEvent


class FakeEventLogRepository:
    """Keeps what a use case logged, so a test can assert on the event object
    itself instead of on a mock's call arguments.

    `FakeUnitOfWork` installs one by default: a use case that records an event
    would otherwise fail every test that predates its logging, and the failure
    would look like a missing repository rather than a new side effect.
    """

    def __init__(self) -> None:
        self.recorded: list[tuple[Actor, DomainEvent]] = []

    async def record(self, session: Any, actor: Actor, event: DomainEvent) -> None:
        self.recorded.append((actor, event))

    @property
    def codes(self) -> list[str]:
        return [event.code for _, event in self.recorded]
