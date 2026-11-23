from uuid import UUID as PY_UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base
from src.core.database.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UUID7IDMixin,
)


class Note(Base, UUID7IDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "notes"
    # Named by hand: the convention derives a name from the first column only.
    # One owner's list is read in this order (`ListQuery`), and the leading
    # column serves the foreign key as well.
    __table_args__ = (
        Index("ix_notes_owner_created_at", "owner_id", "created_at", "id"),
    )

    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, default="")
    # UUIDv7 deliberately: note ids are not enumerable-sensitive; index locality wins.
    owner_id: Mapped[PY_UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    def __repr__(self) -> str:
        return f"<Note(id={self.id}, owner_id={self.owner_id}, title={self.title!r})>"
