from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from src.core.schemas import Base


class NoteCreateModel(Base):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = ""


class NoteUpdateModel(Base):
    title: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = None

    @field_validator("title", "content")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str:
        # PATCH uses exclude_unset semantics: an omitted field never reaches this
        # validator (defaults are not validated), so None here is always an
        # explicit null - and both columns are non-nullable.
        if value is None:
            raise ValueError("Field cannot be null")
        return value


class NoteViewModel(Base):
    id: UUID
    title: str
    content: str
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
