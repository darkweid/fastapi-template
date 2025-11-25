import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
import inspect
import time
from typing import Any, TypeVar, cast

from loggers import get_logger

logger = get_logger(__name__)


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
