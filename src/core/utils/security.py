import asyncio
import random

from passlib.context import CryptContext

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


async def generate_otp() -> str:
    """
    Generate a random OTP
    """
    return str(random.randint(10000, 99999))
