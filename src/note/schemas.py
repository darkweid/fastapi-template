from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator

from src.core.schemas import Base, NulFreeStr, PatchField, TrimmedStr

# `notes.content` is `Text`: the schema is the only ceiling it has.
NOTE_CONTENT_MAX_LENGTH = 10_000
NoteContent = Annotated[NulFreeStr, Field(max_length=NOTE_CONTENT_MAX_LENGTH)]
NoteTitle = Annotated[TrimmedStr, Field(max_length=255)]


class NoteCreateModel(Base):
    title: NoteTitle
    content: NoteContent = ""


class NoteUpdateModel(Base):
    title: PatchField[NoteTitle] = None
    content: PatchField[NoteContent] = None

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
