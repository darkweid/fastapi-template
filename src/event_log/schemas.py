from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import IPvAnyAddress, field_validator

from src.core.pagination import SortableListQueryParams
from src.core.schemas import Base
from src.event_log.enums import ActorType, ObjectType


class EventLogViewModel(Base):
    id: UUID
    actor_type: ActorType
    # Bare identifiers on purpose: resolving them into names means one query
    # per identity store the project has, and only the project knows how many
    # that is. A fork that needs names adds a use case of its own over this.
    actor_id: UUID | None
    object_type: ObjectType | None
    object_id: UUID | None
    event_type: str
    payload: dict[str, Any]
    ip_address: IPvAnyAddress | None
    created_at: datetime


class EventLogListParams(SortableListQueryParams):
    """`created_at` is the only sortable column and nothing here is searchable,
    so this extends the search-free base."""

    actor_type: ActorType | None = None
    actor_id: UUID | None = None
    object_type: ObjectType | None = None
    object_id: UUID | None = None
    event_type: str | None = None

    @field_validator(
        "actor_type",
        "actor_id",
        "object_type",
        "object_id",
        "event_type",
        mode="before",
    )
    @classmethod
    def _blank_exact_filter_to_none(cls, value: Any) -> Any:
        # A form that submits every field sends `?actor_type=` for the ones
        # left empty; without this each of them would be a 422.
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    def to_filters(self) -> dict[str, Any]:
        """The exact-match filters, with the untouched ones left out.

        A `None` here means "not filtered", never "column is null", so it must
        not reach the repository as a filter value.
        """
        return {
            field: value
            for field, value in (
                ("actor_type", self.actor_type),
                ("actor_id", self.actor_id),
                ("object_type", self.object_type),
                ("object_id", self.object_id),
                ("event_type", self.event_type),
            )
            if value is not None
        }
