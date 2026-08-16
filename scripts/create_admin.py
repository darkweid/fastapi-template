"""Bootstrap the first admin account.

Run once against a fresh deployment: `make create-admin`. Reads ADMIN_EMAIL
and ADMIN_PASSWORD from the environment (both required); ADMIN_FIRST_NAME,
ADMIN_LAST_NAME, ADMIN_USERNAME and ADMIN_PHONE are optional, defaulting to
values meant to be edited afterwards through the application itself.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import ValidationError

from loggers import get_logger
from src.core.database.session import tasks_async_session
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.schemas import StrongPasswordValidationMixin
from src.core.utils.security import hash_password, mask_email, normalize_email
from src.user.enums import UserRole

logger = get_logger(__name__)

DEFAULT_FIRST_NAME = "Admin"
DEFAULT_LAST_NAME = "User"
DEFAULT_PHONE_NUMBER = "+10000000000"

USAGE = (
    "Usage: ADMIN_EMAIL=<email> ADMIN_PASSWORD=<password> "
    "python -m scripts.create_admin"
)


class _AdminPasswordModel(StrongPasswordValidationMixin):
    """Validates a candidate admin password with the registration rules."""

    password: str


async def ensure_admin(
    uow: ApplicationUnitOfWork[RepositoryProtocol],
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    username: str,
    phone_number: str,
) -> None:
    """
    Create the first admin user, or promote an existing account to admin.

    Inputs:
    - uow: unit of work wrapping the session to operate on.
    - email, password: credentials for the account.
    - first_name, last_name, username, phone_number: profile fields used only
      when a new user is created.

    Validations:
    - password must satisfy the same strength rules as self-registration,
      checked before any repository call.

    Workflow:
    1) Validate the password and normalize the email.
    2) Look up an existing, non-deleted user by email.
    3) If found, force role=ADMIN and is_active/is_verified=True, leaving the
       stored password hash untouched.
    4) Otherwise create a new user with a freshly hashed password, already
       admin, active and verified.
    5) Commit the transaction.

    Side effects:
    - Creates or updates one row in the users table.

    Errors:
    - pydantic.ValidationError: the password does not meet the strength rules.

    Returns:
    - None.
    """
    _AdminPasswordModel(password=password)
    normalized_email = normalize_email(email)

    async with uow:
        existing_user = await uow.users.get_single(uow.session, email=normalized_email)
        if existing_user:
            await uow.users.update(
                uow.session,
                {"role": UserRole.ADMIN, "is_active": True, "is_verified": True},
                id=existing_user.id,
            )
            await uow.commit()
            logger.info(
                "[Create Admin] %s already exists, ensured admin role.",
                mask_email(normalized_email),
            )
            return

        password_hash = await hash_password(password)
        await uow.users.create(
            uow.session,
            {
                "first_name": first_name,
                "last_name": last_name,
                "email": normalized_email,
                "username": username,
                "phone_number": phone_number,
                "password_hash": password_hash,
                "role": UserRole.ADMIN,
                "is_active": True,
                "is_verified": True,
            },
        )
        await uow.commit()
        logger.info("[Create Admin] %s created as admin.", mask_email(normalized_email))


async def main() -> None:
    email = os.environ.get("ADMIN_EMAIL", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or not password:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)

    local_part = email.split("@", 1)[0]
    first_name = os.environ.get("ADMIN_FIRST_NAME") or DEFAULT_FIRST_NAME
    last_name = os.environ.get("ADMIN_LAST_NAME") or DEFAULT_LAST_NAME
    username = os.environ.get("ADMIN_USERNAME") or local_part
    phone_number = os.environ.get("ADMIN_PHONE") or DEFAULT_PHONE_NUMBER

    async with tasks_async_session() as session:
        uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
        try:
            await ensure_admin(
                uow,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                username=username,
                phone_number=phone_number,
            )
        except ValidationError as exc:
            print(f"Invalid ADMIN_PASSWORD: {exc.errors()[0]['msg']}", file=sys.stderr)
            raise SystemExit(2) from exc


if __name__ == "__main__":
    asyncio.run(main())
