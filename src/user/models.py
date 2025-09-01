from sqlalchemy import String, Boolean, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, validates

from src.core.database.base import Base
from src.core.database.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UUIDIDMixin,
)
from src.core.utils.security import hash_password
from src.user.enums import UserRole


class User(Base, UUIDIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    phone_number: Mapped[str] = mapped_column(String(20))
    password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole), nullable=False, default=UserRole.VIEWER
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    """relationships"""
    # Add relationships here

    @validates("password")
    def validate_password(self, _: str, value: str) -> str:
        if value != self.password:
            value = hash_password(value)
        return value

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return f"<User(id={str(self.id)},first_name={self.first_name!r}, email={self.email!r})"
