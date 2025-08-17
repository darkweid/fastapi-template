import asyncio
import inspect
import random
import time
from datetime import datetime, date, time as datetime_time
from functools import wraps
from typing import Any, TypeVar, cast
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

import pytz
from passlib.context import CryptContext

from src.core.settings import settings
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
    from_date: str | date | datetime | None,
    to_date: str | date | datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """If only `to_date` is provided, both `from_date` and `to_date` will be set to the start and end of that day.
    If both `from_date` and `to_date` are provided, they will be converted to datetime objects
    representing the start and end of their respective days.

    :param from_date: Start date string (format YYYY-MM-DD) or None
    :param to_date: End date string (format YYYY-MM-DD) or None
    :return: Tuple (from_date, to_date) with datetime objects or (None, None) if both are None
    """

    def to_utc(input_date: str | date | datetime, is_end: bool = False) -> datetime:
        """Convert local time to UTC"""
        if isinstance(input_date, str):
            _date = list(map(int, input_date.split("-")))
            time_part = datetime_time.max if is_end else datetime_time.min
            local_dt = LOCAL_TZ.localize(
                datetime.combine(date(_date[0], _date[1], _date[2]), time_part)
            )
        elif isinstance(input_date, datetime):
            local_dt = input_date.astimezone(LOCAL_TZ)

        elif isinstance(input_date, date):
            local_dt = LOCAL_TZ.localize(
                datetime.combine(
                    input_date, datetime_time.max if is_end else datetime_time.min
                )
            )

        return local_dt.astimezone(pytz.utc)  # convert to UTC

    result_from_date: datetime | None = None
    result_to_date: datetime | None = None

    if to_date and not from_date:
        result_from_date = to_utc(to_date, is_end=False)  # Local 00:00 → UTC
        result_to_date = to_utc(to_date, is_end=True)  # Local 23:59:59 → UTC

    elif from_date and to_date:
        result_from_date = to_utc(from_date, is_end=False)
        result_to_date = to_utc(to_date, is_end=True)

    return result_from_date, result_to_date


def get_utc_now() -> datetime:
    """
    Get the current date and time in UTC.

    This function returns the current time with timezone information set to UTC,
    ensuring that the returned datetime object is offset-aware.

    Returns:
        datetime: The current date and time in UTC with tzinfo set to ZoneInfo("UTC").
    """
    return datetime.now(ZoneInfo("UTC"))


F = TypeVar("F", bound=Callable[..., Any])


def with_retries(max_retries: int = 3, delay: int = 2) -> Callable[[F], F]:
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
        The decorator function that wraps the target function with retry logic.

    Raises:
        The last exception is encountered if all retry attempts fail.

    Notes:
        Automatically detects whether the wrapped function is async or sync and handles retries accordingly.
        Only exceptions are retried — if the function returns an incorrect or unexpected result, it will not retry.

    Example:
        @with_retries(max_retries=5, delay=1)
        async def fetch_data(): ...

        @with_retries()
        def read_file(): ...
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                for attempt in range(1, max_retries + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        logger.warning(
                            f"[RETRY] Async function '{func.__name__}' attempt {attempt} failed: {e}"
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(delay * attempt)
                        else:
                            raise

            return cast(F, async_wrapper)
        else:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                for attempt in range(1, max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        logger.warning(
                            f"[RETRY] Sync function '{func.__name__}' attempt {attempt} failed: {e}"
                        )
                        if attempt < max_retries:
                            time.sleep(delay * attempt)
                        else:
                            raise

            return cast(F, sync_wrapper)

    return decorator


T = TypeVar("T")


def with_retries_on_result(
    max_retries: int = 3,
    delay: int = 2,
    success_key: tuple[str, ...] = ("result", "code"),
    expected_value: str = "OK",
) -> Callable[
    [Callable[..., Awaitable[dict[str, Any]]]], Callable[..., Awaitable[dict[str, Any]]]
]:
    """
    A decorator that retries an asynchronous function if the result does not contain an expected value
    at a specified key path.

    Ideal for async API calls that return structured responses, where retry logic depends not on exceptions,
    but on the content of the result (e.g., status codes or result flags).

    The key path is defined as a tuple and used to traverse the nested dictionary in the result.

    Note:
        This decorator must be applied to asynchronous functions only (`async def`).

    Args:
        max_retries (int): Maximum number of retry attempts. Default is 3.
        delay (int): Base delay (in seconds) between retries. Increases linearly. Default is 2.
        success_key (tuple): A tuple representing the key path to check in the result dict. Default is ("result", "code").
        expected_value (Any): The value that indicates success. Default is "OK".

    Returns:
        The result from the decorated async function if it matches the expected value.

    Raises:
        ValueError if the expected value is not found.
        The last exception is raised if all retries fail.
    """

    def decorator(
        func: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            for attempt in range(1, max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    current: Any = result
                    for key in success_key:
                        if not isinstance(current, dict) or key not in current:
                            current = None
                            break
                        current = current.get(key)
                    if current == expected_value:
                        return result
                    else:
                        raise ValueError(f"Unexpected result: {result}")
                except Exception as e:
                    logger.warning(
                        f"[RETRY] Function '{func.__name__}' attempt {attempt} failed: {e}"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(delay * attempt)
                    else:
                        raise
            # This line should not be reachable, but mypy requires it
            raise RuntimeError("Unreachable code")

        return wrapper

    return decorator
