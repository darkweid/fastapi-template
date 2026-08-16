from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from src.core.schemas import Base
from src.core.validations import FULL_NAME_PATTERN
from src.user.enums import UserRole


class UserProfileUpdateModel(Base):
    first_name: str | None = Field(None, min_length=2, max_length=30)
    last_name: str | None = Field(None, min_length=2, max_length=30)
    username: str | None = Field(None, min_length=3, max_length=50)

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not FULL_NAME_PATTERN.match(value):
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
