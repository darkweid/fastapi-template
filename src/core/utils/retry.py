import asyncio
from collections.abc import Callable
from functools import wraps
import inspect
import time
from typing import Any, TypeVar, cast

from loggers import get_logger

logger = get_logger(__name__)


F = TypeVar("F", bound=Callable[..., Any])


def with_retries(max_retries: int = 3, delay: int = 2) -> Callable[[F], F]:
    """
    Retry a sync or async function on exception, with a linearly growing delay
    (`delay * attempt`). The last exception propagates once the attempts run out.

    Only exceptions are retried: a wrong-but-returned result is not a failure here.
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
                            "[RETRY] Async function '%s' attempt %d failed: %s",
                            func.__name__,
                            attempt,
                            e,
                        )
                        if attempt == max_retries:
                            raise
                        await asyncio.sleep(delay * attempt)

            return cast(F, async_wrapper)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.warning(
                        "[RETRY] Sync function '%s' attempt %d failed: %s",
                        func.__name__,
                        attempt,
                        e,
                    )
                    if attempt == max_retries:
                        raise
                    time.sleep(delay * attempt)

        return cast(F, sync_wrapper)

    return decorator
