import asyncio
import inspect
import random
import time
from datetime import datetime, date, time
from functools import wraps
from typing import Optional, Tuple, Union
from zoneinfo import ZoneInfo

import pytz
from passlib.context import CryptContext

from app.core.settings import settings
from loggers import get_logger

logger = get_logger(__name__)

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,  # 64 MB
    argon2__time_cost=3,
    argon2__parallelism=2
)


def hash_password(password: str) -> str:
    """
    Hashes the provided password using Argon2 with the configured parameters.

    :param password: The plaintext password as a string.
    :return: The hashed password as a string.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies that a text password matches its hashed counterpart.

    :param plain_password: The text password provided by the user.
    :param hashed_password: The stored hashed password from the database.
    :return: True if the passwords match, False otherwise.
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        return False


async def generate_otp() -> str:
    """
    Generate a random OTP
    """
    return str(random.randint(10000, 99999))


LOCAL_TZ = pytz.timezone(str(settings.tz))


def parse_date_range(
        from_date: Optional[Union[str, date, datetime]],
        to_date: Optional[Union[str, date, datetime]]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """If only `to_date` is provided, both `from_date` and `to_date` will be set to the start and end of that day.
    If both `from_date` and `to_date` are provided, they will be converted to datetime objects
    representing the start and end of their respective days.

    :param from_date: Start date string (format YYYY-MM-DD) or None
    :param to_date: End date string (format YYYY-MM-DD) or None
    :return: Tuple (from_date, to_date) with datetime objects or (None, None) if both are None
    """

    def to_utc(input_date: Union[str, date, datetime], is_end: bool = False) -> datetime:
        """Convert local time to UTC"""
        if isinstance(input_date, str):
            _date = list(map(int, input_date.split("-")))
            time_part = time.max if is_end else time.min
            local_dt = LOCAL_TZ.localize(datetime.combine(date(_date[0], _date[1], _date[2]), time_part))
        elif isinstance(input_date, date):
            local_dt = LOCAL_TZ.localize(datetime.combine(input_date, time.max if is_end else time.min))
        elif isinstance(input_date, datetime):
            local_dt = input_date.astimezone(LOCAL_TZ)
        else:
            return None
        return local_dt.astimezone(pytz.utc)  # convert to UTC

    if to_date and not from_date:
        from_date = to_utc(to_date, is_end=False)  # Local 00:00 → UTC
        to_date = to_utc(to_date, is_end=True)  # Local 23:59:59 → UTC

    elif from_date and to_date:
        from_date = to_utc(from_date, is_end=False)
        to_date = to_utc(to_date, is_end=True)

    return from_date, to_date


def get_utc_now() -> datetime:
    """
    Get the current date and time in UTC.

    This function returns the current time with timezone information set to UTC,
    ensuring that the returned datetime object is offset-aware.

    Returns:
        datetime: The current date and time in UTC with tzinfo set to ZoneInfo("UTC").
    """
    return datetime.now(ZoneInfo("UTC"))


def with_retries(max_retries=3, delay=2):
    """
    A universal retry decorator for both asynchronous and synchronous functions.

    This decorator retries the wrapped function up to `max_retries` times in case of exceptions.
    For asynchronous functions (`async def`), it uses `await asyncio.sleep(...)` between attempts.
    For synchronous functions (`def`), it uses `time.sleep(...)`.

    Useful for operations that may fail intermittently, such as network requests, database transactions,
    or third-party API calls.

    The delay between retries increases linearly: `delay * attempt_number`.

    Args:
        max_retries (int): Maximum number of retry attempts before raising the last exception. Default is 3.
        delay (int): Base delay in seconds between retries. Default is 2.

    Returns:
        The result of the function if it eventually succeeds within the retry limit.

    Raises:
        The last exception encountered if all retry attempts fail.

    Notes:
        Automatically detects whether the wrapped function is async or sync and handles retries accordingly.
        Only exceptions are retried — if the function returns an incorrect or unexpected result, it will not retry.

    Example:
        @with_retries(max_retries=5, delay=1)
        async def fetch_data(): ...

        @with_retries()
        def read_file(): ...
    """

    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                for attempt in range(1, max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        logger.warning(f"[RETRY] Async function '{func.__name__}' attempt {attempt} failed: {e}")
                        if attempt < max_retries:
                            await asyncio.sleep(delay * attempt)
                        else:
                            raise

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                for attempt in range(1, max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        logger.warning(f"[RETRY] Sync function '{func.__name__}' attempt {attempt} failed: {e}")
                        if attempt < max_retries:
                            time.sleep(delay * attempt)
                        else:
                            raise

            return sync_wrapper

    return decorator
