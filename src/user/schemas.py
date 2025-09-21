from uuid import UUID

from pydantic import EmailStr

from src.core.schemas import Base
from src.user.enums import UserRole


class UserProfileViewModel(Base):
    id: UUID
    first_name: str
    last_name: str
    username: str
    role: UserRole
    email: EmailStr
    phone_number: str
    is_verified: bool


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
