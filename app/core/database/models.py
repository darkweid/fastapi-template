import uuid
from datetime import datetime

from sqlalchemy import func, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column

Base = declarative_base()


class TimestampMixin:
    """
    Add columns to a mapped class
    created_at: DateTime, oncreate trigger
    updated_at: DateTime, onupdate trigger
    """

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=func.now(), onupdate=func.now())


class UUIDIDMixin:
    """
    Add a UUID column to a mapped class
    id: UUID
    """
    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class IntegerIDMixin:
    """
    Add an autoincrement column to a mapped class
    id: Integer
    """
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class SoftDeleteMixin:
    """
    Add columns to a mapped class
    deleted_at: DateTime
    is_deleted: Boolean
    """
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
