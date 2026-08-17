from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from src.core.schemas import Base


class NoteCreateModel(Base):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = ""


class NoteUpdateModel(Base):
    # SkipJsonSchema[None] keeps the field optional at runtime while removing
    # the null branch from the OpenAPI contract - the validator below rejects
    # an explicit null, so the schema must not advertise it. Constraints sit
    # inside the str branch so they never apply to the None default.
    title: (
        Annotated[str, Field(min_length=1, max_length=255)] | SkipJsonSchema[None]
    ) = None
    content: str | SkipJsonSchema[None] = None

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
