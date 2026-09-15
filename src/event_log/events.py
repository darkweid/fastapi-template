from typing import ClassVar
from uuid import UUID

from pydantic import ConfigDict

from src.core.schemas import Base
from src.event_log.enums import ObjectType


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

    object_id: UUID | None = None
