from __future__ import annotations

from uuid import UUID, uuid4

from src.core.utils.security import hash_password
from src.user.enums import UserRole
from src.user.models import User


def build_user(
    *,
    user_id: UUID | None = None,
    email: str = "user@example.com",
    username: str = "user",
    first_name: str = "Test",
    last_name: str = "User",
    phone_number: str = "+10000000000",
    password: str = "password",
    role: UserRole = UserRole.VIEWER,
    is_verified: bool = True,
    is_active: bool = True,
) -> User:
    user = User(
        id=user_id or uuid4(),
        first_name=first_name,
        last_name=last_name,
        email=email,
        username=username,
        phone_number=phone_number,
        password_hash=hash_password(password),
        role=role,
        is_verified=is_verified,
        is_active=is_active,
    )
    return user
