from datetime import datetime
from uuid import UUID

from pydantic import Field

from src.core.schemas import Base


class NoteCreateModel(Base):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = ""


class NoteUpdateModel(Base):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = None


class NoteViewModel(Base):
    id: UUID
    title: str
    content: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
