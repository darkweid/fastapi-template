import asyncio
import hashlib
import random

from passlib.context import CryptContext
from pydantic import EmailStr

from loggers import get_logger

logger = get_logger(__name__)

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,  # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=2,
)


def hash_password(password: str) -> str:
    """
    Hashes the provided password using Argon2 with the configured parameters.

    :param password: The plaintext password as a string.
    :return: The hashed password as a string.
    """
    return pwd_context.hash(password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies that a text password matches its hashed counterpart.

    :param plain_password: The text password provided by the user.
    :param hashed_password: The stored hashed password from the database.
    :return: True if the passwords match, False otherwise.
    """
    try:
        return await asyncio.to_thread(
            pwd_context.verify, plain_password, hashed_password
        )
    except ValueError:
        return False


def generate_otp() -> str:
    """
    Generate a random OTP
    """
    return str(random.randint(10000, 99999))


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
