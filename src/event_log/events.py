from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from pydantic import ConfigDict

from src.core.schemas import Base
from src.event_log.actor import Actor
from src.event_log.enums import ObjectType

# A column of its own in the journal; repeating it inside the payload would let
# the two copies drift apart.
_PAYLOAD_EXCLUDED_FIELDS = {"object_id"}


class DomainEvent(Base):
    """One logged action. Subclass fields become the row's payload.

    A module declares its own events in `<module>/events.py`. `code` is the
    value stored in `event_logs.event_type`, prefixed with that module;
    `tests/unit/src/event_log/test_catalog.py` pins the prefix, the uniqueness
    of the value and that it fits the column.

    Keep a subclass's fields to what a reader of the row needs and what is safe
    to keep after the object it describes is gone: the payload outlives the
    entity, and nothing prunes it.
    """

    model_config = ConfigDict(use_enum_values=False)

    code: ClassVar[str]
    object_type: ClassVar[ObjectType | None] = None
    # False for an occurrence that is nobody's action and tells a reader of the
    # log nothing (a reminder coming due): subscribers get it, the journal not.
    journaled: ClassVar[bool] = True

    object_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PublishedEvent:
    """One occurrence of a `DomainEvent`, as the journal and every subscriber
    see it. `id` names the occurrence everywhere (the journal row's id, a
    subscriber's dedupe key) and `occurred_at` is the one instant they share."""

    id: UUID
    actor: Actor
    event: DomainEvent
    occurred_at: datetime

    def payload(self) -> dict[str, Any]:
        return self.event.model_dump(mode="json", exclude=_PAYLOAD_EXCLUDED_FIELDS)
