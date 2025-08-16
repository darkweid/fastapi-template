import uuid
from datetime import datetime
from uuid import UUID as PY_UUID

from sqlalchemy import MetaData, func, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s"
        }
    )


class TimestampMixin:
    """
    Add columns to a mapped class
    created_at: DateTime, oncreate trigger
    updated_at: DateTime, onupdate trigger
    """
    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=func.now(), onupdate=func.now())


class UUIDIDMixin:
    """
    Add a UUID column to a mapped class
    id: UUID
    """
    __abstract__ = True

    id: Mapped[PY_UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class IntegerIDMixin:
    """
    Add an autoincrement column to a mapped class
    id: Integer
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
