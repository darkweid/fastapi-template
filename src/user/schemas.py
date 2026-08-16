from uuid import UUID

from pydantic import EmailStr, Field

from src.core.schemas import Base
from src.user.enums import UserRole


class UserProfileUpdateModel(Base):
    first_name: str | None = Field(None, min_length=1, max_length=50)
    last_name: str | None = Field(None, min_length=1, max_length=50)
    username: str | None = Field(None, min_length=3, max_length=50)


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
