from uuid import UUID as PY_UUID

from sqlalchemy import ForeignKey, String, Text
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

    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, default="")
    # UUIDv7 deliberately: note ids are not enumerable-sensitive; index locality wins.
    owner_id: Mapped[PY_UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    def __repr__(self) -> str:
        return f"<Note(id={self.id}, owner_id={self.owner_id}, title={self.title!r})>"
