from datetime import datetime
import uuid
from uuid import UUID as PY_UUID

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid6


class TimestampMixin:
    """
    Add columns to a mapped class
    created_at: DateTime, `on create` trigger
    updated_at: DateTime, `on update` trigger
    """

    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )


class UUIDIDMixin:
    """
    Add a UUID column to a mapped class
    id: UUID v4
    """

    __abstract__ = True

    id: Mapped[PY_UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class UUID7IDMixin:
    """
    Add a UUID v7 column to a mapped class
    id: UUID v7 (time-ordered)

    Advantages:
    - Monotonic ordering keeps inserts clustered and improves index locality.
    - Still globally unique across nodes without coordination.

    Disadvantages:
    - Embeds a timestamp, which may leak limited creation timing information.
    """

    __abstract__ = True

    id: Mapped[PY_UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid6.uuid7
    )


class IntegerIDMixin:
    """
    Add an autoincrement column to a mapped class
    id: Integer, autoincrement
    """

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class SoftDeleteMixin:
    """
    Add columns to a mapped class
    deleted_at: DateTime
    is_deleted: Boolean
    """

    __abstract__ = True

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
