from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Enum as SQLEnum, Index, String, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base
from src.core.database.mixins import UUID7IDMixin
from src.event_log.enums import ActorType, ObjectType


class EventLog(Base, UUID7IDMixin):
    """An append-only record of one action.

    No `updated_at` and no soft delete: rows are never rewritten and never
    removed. Nothing may declare a foreign key onto this table and nothing may
    add a unique constraint - both would block partitioning by `created_at`
    later, when this is the largest table in the database. A row therefore
    survives the account it describes, which is the point of an audit trail and
    the reason a payload must not carry secrets.
    """

    __tablename__ = "event_logs"
    # Named by hand, unlike the plain column indexes elsewhere in the project:
    # the naming convention derives no name for an expression index, and the
    # direction has to be spelled out. `ListQuery` orders this table by
    # `created_at DESC NULLS LAST, id DESC`; an index without that ordering
    # makes the planner sort the whole table instead of reading fifty rows.
    __table_args__ = (
        Index(
            "ix_event_logs_actor",
            "actor_type",
            "actor_id",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
        ),
        Index(
            "ix_event_logs_object",
            "object_type",
            "object_id",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
        ),
        Index(
            "ix_event_logs_event_type",
            "event_type",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
        ),
        Index(
            "ix_event_logs_created_at",
            text("created_at DESC NULLS LAST"),
            text("id DESC"),
        ),
    )

    actor_type: Mapped[ActorType] = mapped_column(SQLEnum(ActorType))
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    object_type: Mapped[ObjectType | None] = mapped_column(
        SQLEnum(ObjectType), nullable=True
    )
    object_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # A string, not an enum: the catalog gains members with every feature, and
    # a database enum would turn each of them into a migration. The value never
    # comes from a request - only from a `DomainEvent` subclass.
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    # Written once and never updated, so `TimestampMixin` would add a column
    # this table has no meaning for.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )

    def __repr__(self) -> str:
        return f"<EventLog id={self.id} event_type={self.event_type}>"
