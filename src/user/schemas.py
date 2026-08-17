from typing import Annotated
from uuid import UUID

from pydantic import EmailStr, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from src.core.schemas import Base
from src.core.validations import FULL_NAME_PATTERN
from src.user.enums import UserRole


class UserProfileUpdateModel(Base):
    # SkipJsonSchema[None] keeps the fields optional at runtime while removing
    # the null branch from the OpenAPI contract - the validator below rejects
    # an explicit null, so the schema must not advertise it. Constraints sit
    # inside the str branch so they never apply to the None default.
    first_name: (
        Annotated[str, Field(min_length=2, max_length=30)] | SkipJsonSchema[None]
    ) = None
    last_name: (
        Annotated[str, Field(min_length=2, max_length=30)] | SkipJsonSchema[None]
    ) = None
    username: (
        Annotated[str, Field(min_length=3, max_length=50)] | SkipJsonSchema[None]
    ) = None

    @field_validator("first_name", "last_name", "username")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str:
        # PATCH uses exclude_unset semantics: an omitted field never reaches this
        # validator (defaults are not validated), so None here is always an
        # explicit null - and all three columns are non-nullable.
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not FULL_NAME_PATTERN.match(value):
            raise ValueError(
                "Name must contain latin letters, with single spaces, "
                "hyphens or apostrophes between parts"
            )
        return value


class UserProfileViewModel(Base):
    id: UUID
    first_name: str
    last_name: str
    username: str
    role: UserRole
    email: EmailStr
    phone_number: str
    is_verified: bool
    is_active: bool


class UserSummaryViewModel(Base):
    id: UUID
    first_name: str
    last_name: str
    username: str


class UserSummaryWithContactsViewModel(Base):
    id: UUID
    full_name: str
    username: str
    email: EmailStr
    phone_number: str
