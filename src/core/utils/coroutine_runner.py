"""
Utility helpers to run async code from sync contexts (e.g., Celery tasks).

Notes:
- A module-level reusable event loop is used to avoid "Future attached to a different loop" errors when async drivers (like asyncpg) are accessed from Celery workers.
- In multithreaded scenarios a global loop without synchronization could be unsafe; current usage assumes a single-threaded worker process.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, overload

T = TypeVar("T")
CoroutineFactory = Callable[[], Coroutine[Any, Any, T]]
_loop: asyncio.AbstractEventLoop | None = None


@overload
def execute_coroutine_sync(*, coroutine: Coroutine[Any, Any, T]) -> T: ...


@overload
def execute_coroutine_sync(*, coroutine: CoroutineFactory[T]) -> T: ...


def execute_coroutine_sync(
    *, coroutine: Coroutine[Any, Any, T] | CoroutineFactory[T]
) -> T:
    """
    Run a coroutine in a proper event loop.

    - Reuse existing loop if it exists and is not closed.
    - If no loop exists, or it is closed, create a new one and set it as current.
    """
    coroutine_to_run = coroutine() if callable(coroutine) else coroutine

    loop = _get_or_create_loop()
    return loop.run_until_complete(coroutine_to_run)


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """
    Get a reusable event loop instance or create and register a new one.
    """
    global _loop
    if _loop and not _loop.is_closed():
        asyncio.set_event_loop(_loop)
        return _loop

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    else:
        if loop.is_closed():
            loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)
    _loop = loop
    return loop
