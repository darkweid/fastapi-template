from sqlalchemy import Boolean, Enum as SQLEnum, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from src.core.database.base import Base
from src.core.database.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UUIDIDMixin,
)
from src.core.utils.security import is_password_hash
from src.user.enums import UserRole


class User(Base, UUIDIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "uq_users_email_active_not_deleted",
            "email",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "uq_users_username_not_deleted",
            "username",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )

    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(60))
    phone_number: Mapped[str] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole), nullable=False, default=UserRole.VIEWER
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    """relationships"""
    # Add relationships here

    @validates("password_hash")
    def validate_password_hash(self, _: str, value: str) -> str:
        """
        Validate that the password hash looks like a supported hash format.

        Args:
            _: str
                Unused parameter
            value: str
                The new or updated password hash provided for validation.

        Returns:
            str
                The validated password hash.

        Raises:
            ValueError: If the value is not a valid password hash.
        """
        if not is_password_hash(value):
            raise ValueError("Password hash must be a valid hash.")
        return value

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return (
            f"<User(id={self.id}, first_name={self.first_name!r}, "
            f"email={self.email!r})>"
        )
