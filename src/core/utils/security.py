import asyncio
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import EmailStr

from loggers import get_logger

logger = get_logger(__name__)

password_hasher = PasswordHasher(
    memory_cost=65536,  # 64 MB
    time_cost=3,
    parallelism=2,
)

# Computed once at import. Login verifies against it when the user does not
# exist, so failure timing matches a real password check; it must be a real
# hash produced with the current parameters.
DUMMY_PASSWORD_HASH: str = password_hasher.hash("dummy-password")


async def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2 off the event loop."""
    return await asyncio.to_thread(password_hasher.hash, password)


def is_password_hash(value: str) -> bool:
    """Check whether the given value looks like an Argon2 password hash."""
    return value.startswith("$argon2")


def needs_password_rehash(hashed_password: str) -> bool:
    """True when the hash was made with outdated parameters."""
    return password_hasher.check_needs_rehash(hashed_password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash off the event loop."""
    try:
        return await asyncio.to_thread(
            password_hasher.verify, hashed_password, plain_password
        )
    except (VerificationError, InvalidHashError, ValueError):
        return False


def generate_otp(length: int = 5) -> str:
    """
    Generate a random numeric OTP with a fixed length.
    """
    if length <= 0:
        raise ValueError(f"OTP length must be greater than 0, given {length}.")

    return "".join(secrets.choice("0123456789") for _ in range(length))


def mask_email(email: str | EmailStr) -> str:
    """
    Masks an email address by replacing part of the local and domain parts
    with asterisks.
    Mask pattern: ab***@cd***

    Args:
        email: str
            A string containing the email address to be masked.

    Returns:
        str
            A masked version of the provided email address with part of
            the local and domain obscured.
    """
    try:
        email_str = str(email)
        local, domain = email_str.split("@", 1)
        masked_local = (local[:2] + "***") if local else "*****"
        masked_domain = (domain[:2] + "***") if domain else "*****"
        return f"{masked_local}@{masked_domain}"
    except Exception:
        return "***"


def build_email_throttle_key(prefix: str, email: str | EmailStr) -> str:
    """
    Builds a Redis throttle key based on a normalized email hash.
    """
    email_norm = str(email).lower()
    digest = hashlib.sha256(email_norm.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def normalize_email(email: str) -> str:
    """Normalize an email address."""
    return email.strip().lower()
