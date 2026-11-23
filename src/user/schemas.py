from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, EmailStr, Field, field_validator

from src.core.schemas import Base, PatchField
from src.core.validations import validate_full_name, validate_username
from src.user.enums import UserRole

PersonName = Annotated[
    str, Field(min_length=2, max_length=30), AfterValidator(validate_full_name)
]
# The validator holds the real shape; the length publishes the ceiling and
# refuses an oversized value before any regex runs.
Username = Annotated[str, Field(max_length=60), AfterValidator(validate_username)]


class UserProfileUpdateModel(Base):
    first_name: PatchField[PersonName] = None
    last_name: PatchField[PersonName] = None
    username: PatchField[Username] = None

    @field_validator("first_name", "last_name", "username")
    @classmethod
    def reject_explicit_null(cls, value: str | None) -> str:
        # PATCH uses exclude_unset semantics: an omitted field never reaches this
        # validator (defaults are not validated), so None here is always an
        # explicit null - and all three columns are non-nullable.
        if value is None:
            raise ValueError("Field cannot be null")
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
